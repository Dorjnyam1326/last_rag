import re

from rag.answer_output import GroundingError, ground_answer, renumber_multi_request_citations
from rag.generation import generate_answer, review_evidence
from rag.retrieval import (
    MAX_REUSABLE_PASSAGES,
    deduplicate_passages,
    execute_search_plan,
    previous_turn_passages,
    search_documents,
)
from rag.planning import plan_requests
from rag.routing import (
    INTENT_LABELS,
    MAX_ROUTED_REQUESTS,
    RELATIONSHIP_LABELS,
    build_router_history_context,
    classify_intent,
    get_enabled_restriction,
    get_policy_and_route,
    prepare_history,
)
from rag.selection import select_search_plans

_REQUEST_ID = re.compile(r"R[1-9][0-9]*\Z")
_FACT_ID = re.compile(r"F[1-9][0-9]*\Z")
_PASSAGE_ID = re.compile(r"P[1-9][0-9]*\Z")
_GREETING_RESPONSE = (
    "Сайн байна уу! Ажлын байрны бодлого, HR эсвэл үйл ажиллагааны талаар асуугаарай."
)


def _result(answer: str, status: str) -> dict:
    return {"answer": answer, "citations": [], "status": status, "passages": []}


def _trace_stage(
    process_trace: list[dict],
    stage: str,
    status: str,
    summary: str,
    *,
    request_id: str | None = None,
    details=None,
) -> None:
    event = {
        "stage": stage,
        "status": status,
        "summary": summary,
    }
    if request_id is not None:
        event["request_id"] = request_id
    if details is not None:
        event["details"] = details
    process_trace.append(event)


def _with_process_trace(result: dict, process_trace: list[dict]) -> dict:
    if process_trace:
        result["process_trace"] = process_trace
    return result


def _trace_skipped_stages(
    process_trace: list[dict],
    request_id: str,
    stages: tuple[str, ...],
    reason: str,
) -> None:
    for stage in stages:
        _trace_stage(
            process_trace,
            stage,
            "skipped",
            reason,
            request_id=request_id,
        )


def _unique_evidence_passages(passages: list[dict]) -> list[dict]:
    unique = deduplicate_passages(passages)
    return [
        {**passage, "passage_id": f"P{index}"}
        for index, passage in enumerate(unique, start=1)
    ]


def decide_evidence_sufficiency(
    assessment: dict, planning_proposal: dict, passages: list[dict]
) -> dict:
    """Validate an evidence assessment and decide sufficiency deterministically."""
    if not isinstance(assessment, dict) or not isinstance(planning_proposal, dict):
        raise ValueError("invalid evidence assessment")
    request_id = planning_proposal.get("request_id")
    required_facts = planning_proposal.get("required_facts")
    if (
        not isinstance(request_id, str)
        or not _REQUEST_ID.fullmatch(request_id)
        or assessment.get("request_id") != request_id
        or not isinstance(required_facts, list)
        or not required_facts
        or not isinstance(passages, list)
    ):
        raise ValueError("invalid evidence assessment")

    fact_ids = []
    for fact in required_facts:
        if (
            not isinstance(fact, dict)
            or set(fact) != {"id", "description"}
            or not isinstance(fact.get("id"), str)
            or not _FACT_ID.fullmatch(fact["id"])
            or fact["id"] in fact_ids
            or not isinstance(fact.get("description"), str)
            or not fact["description"].strip()
        ):
            raise ValueError("invalid evidence assessment")
        fact_ids.append(fact["id"])

    review_status = assessment.get("review_status")
    retrieval_status = assessment.get("retrieval_status")
    if (
        not isinstance(review_status, str)
        or review_status not in {"complete", "partial"}
        or not isinstance(retrieval_status, str)
        or retrieval_status not in {"success", "empty", "partial_error"}
        or not isinstance(assessment.get("facts"), list)
        or not isinstance(assessment.get("conflicts"), list)
        or not isinstance(assessment.get("selected_evidence_ids"), list)
        or not isinstance(assessment.get("missing_information"), str)
        or not isinstance(assessment.get("retry_query"), str)
    ):
        raise ValueError("invalid evidence assessment")

    passage_ids = []
    for passage in passages:
        if not isinstance(passage, dict):
            raise ValueError("invalid evidence assessment")
        passage_id = passage.get("passage_id")
        if (
            not isinstance(passage_id, str)
            or not _PASSAGE_ID.fullmatch(passage_id)
            or passage_id in passage_ids
        ):
            raise ValueError("invalid evidence assessment")
        passage_ids.append(passage_id)
    available_ids = set(passage_ids)

    assessed_facts = {}
    for fact in assessment["facts"]:
        if not isinstance(fact, dict) or set(fact) != {
            "fact_id", "status", "evidence_ids", "missing_detail"
        }:
            raise ValueError("invalid evidence assessment")
        fact_id = fact.get("fact_id")
        status = fact.get("status")
        evidence_ids = fact.get("evidence_ids")
        detail = fact.get("missing_detail")
        if (
            not isinstance(fact_id, str)
            or fact_id not in fact_ids
            or fact_id in assessed_facts
            or not isinstance(status, str)
            or status not in {"supported", "partial", "missing"}
            or not isinstance(evidence_ids, list)
            or any(not isinstance(evidence_id, str) for evidence_id in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
            or any(
                not isinstance(evidence_id, str)
                or not _PASSAGE_ID.fullmatch(evidence_id)
                or evidence_id not in available_ids
                for evidence_id in evidence_ids
            )
            or (status in {"supported", "partial"} and not evidence_ids)
            or (status == "missing" and evidence_ids)
            or (status == "supported" and detail is not None)
            or (
                status != "supported"
                and (not isinstance(detail, str) or not detail.strip())
            )
        ):
            raise ValueError("invalid evidence assessment")
        assessed_facts[fact_id] = fact
    if set(assessed_facts) != set(fact_ids):
        raise ValueError("invalid evidence assessment")

    conflict_facts = set()
    selected_ids = list(dict.fromkeys(
        evidence_id
        for fact_id in fact_ids
        for evidence_id in assessed_facts[fact_id]["evidence_ids"]
    ))
    conflict_keys = set()
    for conflict in assessment["conflicts"]:
        if not isinstance(conflict, dict) or set(conflict) != {
            "fact_id", "evidence_ids", "reason"
        }:
            raise ValueError("invalid evidence assessment")
        fact_id = conflict.get("fact_id")
        evidence_ids = conflict.get("evidence_ids")
        reason = conflict.get("reason")
        if (
            not isinstance(fact_id, str)
            or fact_id not in assessed_facts
            or not isinstance(evidence_ids, list)
            or any(not isinstance(evidence_id, str) for evidence_id in evidence_ids)
            or len(evidence_ids) < 2
            or len(set(evidence_ids)) != len(evidence_ids)
            or any(
                not isinstance(evidence_id, str)
                or not _PASSAGE_ID.fullmatch(evidence_id)
                or evidence_id not in available_ids
                for evidence_id in evidence_ids
            )
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("invalid evidence assessment")
        conflict_key = (fact_id, frozenset(evidence_ids))
        if conflict_key in conflict_keys:
            raise ValueError("invalid evidence assessment")
        conflict_keys.add(conflict_key)
        conflict_facts.add(fact_id)
        for evidence_id in evidence_ids:
            if evidence_id not in selected_ids:
                selected_ids.append(evidence_id)

    supplied_selected_ids = assessment["selected_evidence_ids"]
    if (
        any(not isinstance(evidence_id, str) for evidence_id in supplied_selected_ids)
        or len(set(supplied_selected_ids)) != len(supplied_selected_ids)
        or any(
            not isinstance(evidence_id, str)
            or not _PASSAGE_ID.fullmatch(evidence_id)
            or evidence_id not in available_ids
            for evidence_id in supplied_selected_ids
        )
        or supplied_selected_ids != selected_ids
    ):
        raise ValueError("invalid evidence assessment")

    supported_fact_ids = [
        fact_id
        for fact_id in fact_ids
        if assessed_facts[fact_id]["status"] == "supported"
        and fact_id not in conflict_facts
    ]
    unresolved_fact_ids = [
        fact_id for fact_id in fact_ids if fact_id not in supported_fact_ids
    ]
    reason_codes = []
    if any(fact["status"] == "partial" for fact in assessed_facts.values()):
        reason_codes.append("partial_support")
    if any(fact["status"] == "missing" for fact in assessed_facts.values()):
        reason_codes.append("missing_support")
    if conflict_facts:
        reason_codes.append("unresolved_conflict")
    if review_status != "complete" and retrieval_status != "partial_error":
        reason_codes.append("review_incomplete")
    if retrieval_status == "partial_error":
        reason_codes.append("retrieval_incomplete")

    return {
        "request_id": request_id,
        "sufficient": not reason_codes,
        "supported_fact_ids": supported_fact_ids,
        "unresolved_fact_ids": unresolved_fact_ids,
        "selected_evidence_ids": supplied_selected_ids,
        "reason_codes": reason_codes,
    }


def _search_passages(
    query: str,
    retrieval_provider,
    eligible_file_ids,
    *,
    limit: int = 5,
    search_budget: dict | None = None,
) -> list[dict]:
    if eligible_file_ids is not None and not eligible_file_ids:
        return []
    if search_budget is not None:
        if search_budget["remaining"] <= 0:
            raise RuntimeError("message search budget is exhausted")
        search_budget["remaining"] -= 1
    passages = retrieval_provider(query, limit=limit)
    if not isinstance(passages, list):
        raise TypeError("retrieval provider must return a list of passages")
    return deduplicate_passages(
        [
            passage
            for passage in passages
            if isinstance(passage, dict)
            and (
                eligible_file_ids is None
                or passage.get("file_id") in eligible_file_ids
            )
        ]
    )


def _targeted_retry(
    assessment: dict,
    decision: dict,
    planning_proposal: dict,
    resolved_question: str,
    initial_queries: list[str],
) -> tuple[str | None, list[str], str | None]:
    """Build one deterministic retry query for an unresolved Planner need."""
    unresolved_ids = set(decision["unresolved_fact_ids"])
    assessed = {fact["fact_id"]: fact for fact in assessment["facts"]}
    retryable_ids = {
        fact_id
        for fact_id in unresolved_ids
        if assessed[fact_id]["status"] in {"partial", "missing"}
    }
    if not retryable_ids:
        if "unresolved_conflict" in decision["reason_codes"]:
            return None, [], "unresolved_conflict_only"
        if assessment.get("retrieval_status") == "partial_error":
            return None, [], "retrieval_incomplete_no_unresolved_facts"
        return None, [], "no_unresolved_facts"

    normalized_initial = {
        " ".join(query.casefold().split()) for query in initial_queries
    }
    duplicate_target_ids = []
    for need in planning_proposal["information_needs"]:
        targets = [
            fact_id
            for fact_id in need["targets"]
            if fact_id in retryable_ids
        ]
        if not targets:
            continue
        parts = []
        for part in (
            need["focus"],
            *(assessed[fact_id]["missing_detail"] for fact_id in targets),
            resolved_question,
        ):
            normalized_part = " ".join(part.casefold().split())
            if normalized_part and normalized_part not in {
                " ".join(existing.casefold().split()) for existing in parts
            }:
                parts.append(part)
        query = " ".join(" ".join(parts).split())
        if not query.strip():
            continue
        if " ".join(query.casefold().split()) in normalized_initial:
            duplicate_target_ids = targets
            continue
        return query, targets, None

    if duplicate_target_ids:
        return None, duplicate_target_ids, "duplicate_retry_query"
    return None, [], "no_matching_information_need"


def _selected_turn_passages(
    history: list[dict] | None,
    related_turn_ids: list[str],
    eligible_file_ids: frozenset[str] | None,
) -> list[dict]:
    if not isinstance(history, list):
        return []

    passages = []
    for turn_id in related_turn_ids:
        assistant_message = next(
            (
                message
                for message in history
                if isinstance(message, dict)
                and message.get("role") == "assistant"
                and message.get("turn_id") == turn_id
            ),
            None,
        )
        if assistant_message:
            passages.extend(previous_turn_passages([assistant_message], eligible_file_ids))
        if len(passages) >= MAX_REUSABLE_PASSAGES:
            break
    return deduplicate_passages(passages)[:MAX_REUSABLE_PASSAGES]


def _process_request(
    request: dict,
    catch_errors: bool,
    previous_passages: list[dict] | None = None,
    retrieval_provider=None,
    eligible_file_ids: frozenset[str] | None = None,
    model: str | None = None,
    api_key: str | None = None,
    planning_proposal: dict | None = None,
    stage_outcome: dict | None = None,
    search_budget: dict | None = None,
    process_trace: list[dict] | None = None,
    request_id: str | None = None,
) -> dict:
    if process_trace is None:
        process_trace = []
    trace_request_id = request_id or (planning_proposal or {}).get("request_id") or (
        stage_outcome or {}
    ).get("request_id", "R1")
    intent = request["intent"]
    policy_route = get_policy_and_route(intent)
    if policy_route["policy"] == "restricted":
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Policy restriction-ийн улмаас цааш боловсруулахгүй.",
        )
        restriction = get_enabled_restriction(intent)
        return _result(restriction["response"], "restricted")
    route = policy_route["route"]

    effective_question = request["resolved_question"]
    responses = {
        "out_of_scope": (
            "out_of_scope",
            "Энэ туслах зөвхөн ажлын байрны бодлого, HR болон үйл ажиллагааны сэдэвт тусална.",
        ),
        "direct": (
            "greeting",
            _GREETING_RESPONSE,
        ),
    }
    if route in responses:
        status, answer = responses[route]
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            f"Router route нь {route}; retrieval/generation шаардлагагүй.",
        )
        return _result(answer, status)

    if not isinstance(stage_outcome, dict):
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "SearchPlan-ийн stage outcome ирээгүй.",
        )
        return _result(
            "Хайлтын төлөвлөгөө бэлдээгүй тул хүсэлтийг боловсруулж чадсангүй.",
            "error",
        )
    if stage_outcome.get("status") == "planning_limitation":
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "SSS limitation тул executable search plan үүсээгүй.",
        )
        return {
            **_result(
                "Хайлтын шаардлагыг хангах төлөвлөгөө гаргаж чадсангүй.", "error"
            ),
            "planning_status": "planning_limitation",
            "limitation": stage_outcome.get("reason", "unsupported search requirements"),
        }
    if stage_outcome.get("status") == "retrieval_error":
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Retrieval executor эхний хайлтаа амжилттай дуусгаагүй.",
        )
        return {
            **_result("Баримтын хайлтыг эхлүүлж чадсангүй.", "error"),
            "retrieval_status": "error",
            "error_type": stage_outcome.get("error_type"),
        }

    retrieval = stage_outcome.get("retrieval")
    if not isinstance(retrieval, dict):
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Retrieval буруу форматтай үр дүн буцаасан.",
        )
        return _result("Баримтын хайлтын үр дүн буруу байна.", "error")
    retrieval_status = retrieval.get("status")
    retrieval_passages = retrieval.get("passages")
    if not isinstance(retrieval_passages, list):
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Retrieval passages жагсаалт биш байсан.",
        )
        return _result("Баримтын хайлтын үр дүн буруу байна.", "error")
    if retrieval_status == "error" or (
        retrieval_status == "partial_error" and not retrieval_passages
    ):
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Эхний Retrieval үр дүн бүхэлдээ алдаатай байсан тул дараагийн шат ажиллаагүй.",
        )
        return {
            **_result(
                "Баримтын хайлт амжилтгүй болсон тул хариулт боловсруулсангүй.",
                "error",
            ),
            "retrieval_status": retrieval_status,
            "searches": retrieval.get("searches", []),
            "passages": retrieval.get("passages", []),
        }
    if retrieval_status not in {"success", "empty", "partial_error"}:
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Retrieval status contract буруу байсан.",
        )
        return _result("Баримтын хайлтын төлөвийг баталгаажуулж чадсангүй.", "error")
    if (
        not isinstance(planning_proposal, dict)
        or not isinstance(planning_proposal.get("required_facts"), list)
    ):
        _trace_skipped_stages(
            process_trace,
            trace_request_id,
            (
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Post-retry evidence sufficiency",
                "Generation",
                "Grounding & citations",
            ),
            "Planner proposal буруу байсан тул нотолгооны review эхлүүлээгүй.",
        )
        return _result("Шаардлагатай facts-ийг баталгаажуулж чадсангүй.", "error")

    search_plan = stage_outcome["search_plan"]
    initial_queries = [query["text"] for query in search_plan["queries"]]
    retrieved_passage_count = len(retrieval_passages)
    passages = _unique_evidence_passages(retrieval_passages)
    reused_passage_count = 0
    retry_error_type = None
    final_retrieval_status = retrieval_status
    final_assessment = None

    try:
        search = (
            retrieval_provider
            if retrieval_provider is not None
            else search_documents
        )
        if request["relationship"] == "follow_up":
            reused_passage_count = len(previous_passages or [])
            passages = _unique_evidence_passages(
                [*passages, *(previous_passages or [])]
            )
        _trace_stage(
            process_trace,
            "Deduplication",
            "completed",
            "Retrieval болон холбогдох previous-turn evidence-ийг ID/provenance-ийг нэгтгэн давхардлыг арилгав.",
            request_id=planning_proposal["request_id"],
            details={
                "retrieved_passage_count": retrieved_passage_count,
                "reused_turn_passage_count": reused_passage_count,
                "unique_passage_count": len(passages),
                "duplicates_removed": (
                    retrieved_passage_count + reused_passage_count - len(passages)
                ),
                "passage_ids": [passage["passage_id"] for passage in passages],
            },
        )
        review = review_evidence(
            effective_question,
            passages,
            request_id=planning_proposal["request_id"],
            required_facts=planning_proposal["required_facts"],
            information_needs=planning_proposal["information_needs"],
            retrieval_status=retrieval_status,
            model=model,
            api_key=api_key,
        )
        if review.get("review_status") in {"error", "unavailable"}:
            _trace_stage(
                process_trace,
                "Evidence review",
                "failed",
                "Анхны нотолгооны үнэлгээ амжилтгүй.",
                request_id=planning_proposal["request_id"],
                details={
                    "review_status": review.get("review_status"),
                    "error_type": review.get("error_type"),
                },
            )
            _trace_skipped_stages(
                process_trace,
                planning_proposal["request_id"],
                (
                    "Evidence sufficiency",
                    "Targeted retry",
                    "Updated evidence review",
                    "Post-retry evidence sufficiency",
                    "Generation",
                    "Grounding & citations",
                ),
                "Evidence review амжилтгүй болсон тул дараагийн шат ажиллаагүй.",
            )
            return {
                **_result("Баримтын хүрэлцээг шалгаж чадсангүй.", "error"),
                "passages": passages,
                "retrieval_status": retrieval_status,
                "searches": retrieval.get("searches", []),
                "retry_outcome": {
                    "request_id": planning_proposal["request_id"],
                    "status": "skipped",
                    "target_fact_ids": [],
                    "retry_query_id": None,
                    "new_passage_ids": [],
                    "search_calls_used": 0,
                    "reason": "reviewer_error",
                },
            }
        _trace_stage(
            process_trace,
            "Evidence review",
            "completed" if review.get("review_status") == "complete" else "partial",
            "Эхний хайлтын нотолгоог Planner-ийн facts-тэй тулгав.",
            request_id=planning_proposal["request_id"],
            details={
                "retrieval_status": retrieval_status,
                "passage_count": len(passages),
                "review_status": review.get("review_status"),
                "facts": review.get("facts", []),
                "conflicts": review.get("conflicts", []),
                "selected_evidence_ids": review.get("selected_evidence_ids", []),
            },
        )
        decision = decide_evidence_sufficiency(review, planning_proposal, passages)
        _trace_stage(
            process_trace,
            "Evidence sufficiency",
            "completed" if decision["sufficient"] else "partial",
            "Deterministic Python шалгалтаар дараагийн алхмыг шийдэв.",
            request_id=planning_proposal["request_id"],
            details=decision,
        )
        retry_outcome = {
            "request_id": planning_proposal["request_id"],
            "status": "skipped",
            "target_fact_ids": [],
            "retry_query_id": None,
            "new_passage_ids": [],
            "search_calls_used": 0,
            "reason": "evidence_sufficient",
        }
        retry_query = None
        retry_result_count = 0
        updated_review_status = "skipped"
        updated_review_reason = "retry_not_needed"
        post_retry_decision = None
        if not decision["sufficient"]:
            retry_query, target_fact_ids, skip_reason = _targeted_retry(
                review,
                decision,
                planning_proposal,
                effective_question,
                initial_queries,
            )
            retry_outcome["target_fact_ids"] = target_fact_ids
            if skip_reason:
                retry_outcome["reason"] = skip_reason
            elif eligible_file_ids is not None and not eligible_file_ids:
                retry_outcome["reason"] = "no_eligible_sources"
            elif search_budget is None:
                retry_outcome["reason"] = "search_budget_unavailable"
            elif search_budget["remaining"] <= 0:
                retry_outcome["reason"] = "search_budget_exhausted"
            else:
                retry_outcome.update(
                    status="performed",
                    retry_query_id="RQ1",
                    search_calls_used=1,
                    reason=None,
                )
        if retry_outcome["status"] != "performed":
            updated_review_reason = retry_outcome["reason"]
        if retry_outcome["status"] == "performed":
            try:
                additional = _search_passages(
                    retry_query,
                    search,
                    eligible_file_ids,
                    search_budget=search_budget,
                )
            except Exception as error:
                retry_error_type = type(error).__name__
                final_retrieval_status = "partial_error"
                additional = []
                retry_outcome.update(status="failed", reason="provider_error")
            retry_result_count = len(additional)
            additional = [
                {
                    **passage,
                    "query_id": "RQ1",
                    "rank": rank,
                    "targets": list(target_fact_ids),
                }
                for rank, passage in enumerate(additional, start=1)
            ]
            initial_passage_count = len(passages)
            combined_passage_count = len(passages) + len(additional)
            merged_passages = _unique_evidence_passages([*passages, *additional])
            retry_duplicates_removed = combined_passage_count - len(merged_passages)
            added = len(merged_passages) > len(passages)
            passages = merged_passages
            retry_outcome["new_passage_ids"] = [
                passage["passage_id"]
                for passage in passages[initial_passage_count:]
            ]
            if not retry_error_type and not additional:
                retry_outcome["reason"] = "empty_results"
            elif not retry_error_type and not added:
                retry_outcome["reason"] = "duplicate_results_only"
            _trace_stage(
                process_trace,
                "Targeted retry",
                "failed" if retry_outcome["status"] == "failed" else "completed",
                "Нэмэлт хайлтын оролдлогын үр дүн.",
                request_id=planning_proposal["request_id"],
                details={
                    **retry_outcome,
                    "query": retry_query,
                    "result_count": retry_result_count,
                    "duplicates_removed": retry_duplicates_removed,
                    "unique_passage_count": len(passages),
                    "remaining_search_budget": (
                        search_budget.get("remaining")
                        if isinstance(search_budget, dict)
                        else None
                    ),
                },
            )
            if added:
                if retrieval_status == "empty":
                    final_retrieval_status = "success"
                retry_review = review_evidence(
                    effective_question,
                    passages,
                    request_id=planning_proposal["request_id"],
                    required_facts=planning_proposal["required_facts"],
                    information_needs=planning_proposal["information_needs"],
                    retrieval_status=final_retrieval_status,
                    model=model,
                    api_key=api_key,
                )
                if retry_review.get("review_status") in {"error", "unavailable"}:
                    updated_review_status = "failed"
                    _trace_stage(
                        process_trace,
                        "Updated evidence review",
                        "failed",
                        "Retry-ийн дараах шинэ evidence-ийн үнэлгээ амжилтгүй.",
                        request_id=planning_proposal["request_id"],
                        details={"review_status": retry_review.get("review_status")},
                    )
                    _trace_skipped_stages(
                        process_trace,
                        planning_proposal["request_id"],
                        (
                            "Post-retry evidence sufficiency",
                            "Generation",
                            "Grounding & citations",
                        ),
                        "Updated evidence review алдаатай байсан тул processing зогсов.",
                    )
                    return {
                        **_result("Баримтын хүрэлцээг шалгаж чадсангүй.", "error"),
                        "passages": passages,
                        "retrieval_status": final_retrieval_status,
                        "searches": retrieval.get("searches", []),
                        "retry_outcome": retry_outcome,
                    }
                review = retry_review
                updated_review_status = "completed"
                updated_review_reason = None
                _trace_stage(
                    process_trace,
                    "Updated evidence review",
                    "completed" if review.get("review_status") == "complete" else "partial",
                    "Retry-ээр нэмэгдсэн нотолгоог дахин үнэлэв.",
                    request_id=planning_proposal["request_id"],
                    details={
                        "retrieval_status": final_retrieval_status,
                        "passage_count": len(passages),
                        "review_status": review.get("review_status"),
                        "facts": review.get("facts", []),
                        "conflicts": review.get("conflicts", []),
                        "selected_evidence_ids": review.get("selected_evidence_ids", []),
                    },
                )
            decision_assessment = (
                {**review, "retrieval_status": final_retrieval_status}
                if retry_error_type
                else review
            )
            decision = decide_evidence_sufficiency(
                decision_assessment, planning_proposal, passages
            )
            post_retry_decision = decision
            final_assessment = decision_assessment
        else:
            final_assessment = review

        if retry_outcome["status"] != "performed":
            _trace_stage(
                process_trace,
                "Targeted retry",
                "skipped",
                "Нөхцөл хангаагүй тул нэмэлт retrieval ажиллуулсангүй.",
                request_id=planning_proposal["request_id"],
                details={
                    **retry_outcome,
                    "remaining_search_budget": (
                        search_budget.get("remaining")
                        if isinstance(search_budget, dict)
                        else None
                    ),
                },
            )
        if updated_review_status == "skipped":
            _trace_stage(
                process_trace,
                "Updated evidence review",
                "skipped",
                "Retry-ийн дараа дахин үнэлэх шинэ evidence байгаагүй.",
                request_id=planning_proposal["request_id"],
                details={"reason": updated_review_reason},
            )
        if post_retry_decision is not None:
            _trace_stage(
                process_trace,
                "Post-retry evidence sufficiency",
                "completed" if post_retry_decision["sufficient"] else "partial",
                "Шинэ review-ийн дараа deterministic шийдвэрийг дахин гаргав.",
                request_id=planning_proposal["request_id"],
                details=post_retry_decision,
            )

        relevant = [
            passage
            for passage in passages
            if passage["passage_id"] in decision["selected_evidence_ids"]
        ]
        generation_context = {
            "request_id": planning_proposal["request_id"],
            "required_facts": planning_proposal["required_facts"],
            "evidence_assessment": final_assessment,
            "evidence_decision": decision,
            "model": model,
            "api_key": api_key,
        }
        # Preserve the existing partial-generation input for reused evidence
        # when this request's own search completed with no new passages.
        try:
            if decision["sufficient"] and final_retrieval_status != "empty":
                generated = generate_answer(effective_question, relevant, **generation_context)
            elif (
                decision["selected_evidence_ids"]
                or review["conflicts"]
                or review["review_status"] == "partial"
                or decision["sufficient"]
                or final_retrieval_status == "partial_error"
            ):
                generated = generate_answer(
                    effective_question,
                    relevant,
                    evidence_status="partial",
                    missing_information=final_assessment.get("missing_information", ""),
                    **generation_context,
                )
            else:
                generated = generate_answer(effective_question, [], **generation_context)
        except Exception as error:
            _trace_stage(
                process_trace,
                "Generation",
                "failed",
                "Хариулт үүсгэх дуудлага алдаа буцаав.",
                request_id=planning_proposal["request_id"],
                details={"error_type": type(error).__name__},
            )
            _trace_skipped_stages(
                process_trace,
                planning_proposal["request_id"],
                ("Grounding & citations",),
                "Generation дуудлага амжилтгүй болсон.",
            )
            return {
                **_result("Хариулт үүсгэхэд алдаа гарлаа.", "error"),
                "passages": passages,
                "evidence": [],
                "retrieval_status": final_retrieval_status,
                "retry_outcome": retry_outcome,
            }

        generation_failed = generated.get("status") == "error"
        _trace_stage(
            process_trace,
            "Generation",
            "failed" if generation_failed else "completed",
            "Grounded answer draft үүсгэсэн.",
            request_id=planning_proposal["request_id"],
            details={
                "outcome": generated.get("outcome", generated.get("status", "completed")),
                "error_type": generated.get("error_type"),
                "selected_evidence_ids": decision["selected_evidence_ids"],
                "missing_information": final_assessment.get("missing_information", ""),
            },
        )

        if generated.get("status") == "error":
            grounded = {
                "answer": generated.get("answer", "Хариулт үүсгэхэд алдаа гарлаа."),
                "citations": [],
                "status": "error",
            }
        else:
            try:
                grounded = ground_answer(
                    generated,
                    request_id=planning_proposal["request_id"],
                    required_facts=planning_proposal["required_facts"],
                    assessment=final_assessment,
                    decision=decision,
                    passages=relevant,
                )
            except GroundingError as error:
                grounded = {
                    "answer": "Үүсгэсэн хариултын эх сурвалжийг баталгаажуулж чадсангүй.",
                    "citations": [],
                    "status": "error",
                }
                _trace_stage(
                    process_trace,
                    "Grounding & citations",
                    "failed",
                    "Generation output-ийг нотолгоотой холбож баталгаажуулж чадсангүй.",
                    request_id=planning_proposal["request_id"],
                    details={"error_type": type(error).__name__},
                )
            else:
                _trace_stage(
                    process_trace,
                    "Grounding & citations",
                    "completed",
                    "AnswerDraft-ийг шалгаж, баталгаажсан citations үүсгэв.",
                    request_id=planning_proposal["request_id"],
                    details={
                        "status": grounded.get("status"),
                        "draft_outcome": generated.get("outcome"),
                        "citations": [
                            {
                                "label": citation.get("label"),
                                "filename": citation.get("filename"),
                                "passage_id": citation.get("passage_id"),
                            }
                            for citation in grounded.get("citations", [])
                        ],
                    },
                )
        if generation_failed:
            _trace_stage(
                process_trace,
                "Grounding & citations",
                "skipped",
                "Generation алдаатай тул grounding ажиллуулаагүй.",
                request_id=planning_proposal["request_id"],
            )
        cited_passage_ids = {citation["passage_id"] for citation in grounded["citations"]}
        evidence = [passage for passage in relevant if passage["passage_id"] in cited_passage_ids]
        result = {
            **grounded,
            "passages": passages,
            "evidence": evidence,
            "retrieval_status": final_retrieval_status,
            "retry_outcome": retry_outcome,
        }
        if retrieval_status == "partial_error":
            result["searches"] = retrieval.get("searches", [])
        if retry_error_type:
            result["retry_error_type"] = retry_error_type
        return result
    except Exception as error:
        if not catch_errors:
            raise
        _trace_stage(
            process_trace,
            "Request processing",
            "failed",
            "Request боловсруулах үеийн алдаа.",
            request_id=(planning_proposal or {}).get("request_id"),
            details={"error_type": type(error).__name__},
        )
        return _result("Тухайн хүсэлтийг боловсруулах үед алдаа гарлаа.", "error")


def answer_question(
    question: str,
    history: list[dict] | None = None,
    *,
    retrieval_provider=None,
    eligible_file_ids=None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    if not isinstance(question, str):
        return _result("Асуултыг текст хэлбэрээр оруулна уу.", "error")
    if not question.strip():
        return _result("Асуултаа бичнэ үү.", "clarification")

    if retrieval_provider is not None and eligible_file_ids is None:
        return _result("Баримтын хайлтын зөвшөөрөгдсөн хүрээ тохируулагдаагүй.", "error")
    if eligible_file_ids is not None and retrieval_provider is None:
        return _result("Зөвшөөрөгдсөн хайлтын provider тохируулагдаагүй.", "error")
    try:
        if isinstance(eligible_file_ids, (str, bytes)):
            raise ValueError("eligible_file_ids must be a collection of OpenAI file IDs")
        eligible_ids = (
            frozenset(eligible_file_ids) if eligible_file_ids is not None else None
        )
        if eligible_ids is not None and any(
            not isinstance(file_id, str) or not file_id for file_id in eligible_ids
        ):
            raise ValueError("eligible_file_ids must contain OpenAI file IDs")
    except (TypeError, ValueError):
        return _result("Баримтын хайлтын зөвшөөрөгдсөн хүрээ буруу байна.", "error")

    process_trace = []
    _trace_stage(
        process_trace,
        "Question & History guard",
        "completed",
        "Асуулт текст бөгөөд хоосон биш; эх асуултыг хэвээр Router-д дамжуулав.",
        details={
            "original_question": question,
            "history_message_count": len(history) if isinstance(history, list) else 0,
        },
    )
    try:
        routing = classify_intent(question, history, model=model, api_key=api_key)
        requests = routing["requests"]
        if not isinstance(requests, list) or not requests:
            raise ValueError("router returned no requests")
    except Exception as error:
        _trace_stage(
            process_trace,
            "Router",
            "failed",
            "Асуултыг Router-ийн request болгох үед алдаа гарсан.",
            details={"error_type": type(error).__name__},
        )
        return _with_process_trace(
            _result("Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.", "error"),
            process_trace,
        )

    if any(
        not isinstance(request, dict)
        or request.get("intent") not in INTENT_LABELS
        or request.get("relationship") not in RELATIONSHIP_LABELS
        for request in requests
    ):
        _trace_stage(
            process_trace,
            "Router validation",
            "failed",
            "Router-ийн intent эсвэл relationship contract буруу байсан.",
        )
        return _with_process_trace(
            _result("Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.", "error"),
            process_trace,
        )

    prepared_history = prepare_history(history)
    _, _, allowed_turn_ids = build_router_history_context(prepared_history)
    for index, request in enumerate(requests, start=1):
        related_turn_ids = request.get("related_turn_ids", [])
        if (
            not isinstance(related_turn_ids, list)
            or any(not isinstance(turn_id, str) for turn_id in related_turn_ids)
            or len(set(related_turn_ids)) != len(related_turn_ids)
            or any(turn_id not in allowed_turn_ids for turn_id in related_turn_ids)
            or (request["relationship"] == "follow_up" and not related_turn_ids)
            or (request["relationship"] != "follow_up" and related_turn_ids)
        ):
            _trace_stage(
                process_trace,
                "Router history validation",
                "failed",
                "Router-ийн related turn reference-үүд зөвшөөрөгдсөн history-тэй таарсангүй.",
                request_id=f"R{index}",
            )
            return _with_process_trace(
                _result(
                    "Асуултын холбоотой өмнөх яриаг баталгаажуулж чадсангүй. Тодруулна уу.",
                    "clarification",
                ),
                process_trace,
            )

    if any(request["relationship"] == "clarification" for request in requests):
        _trace_stage(
            process_trace,
            "Router",
            "partial",
            "Request-үүдийг найдвартай салгаж/шийдвэрлэж чадаагүй тул хайлт хийгээгүй.",
            details=[
                {
                    "request_id": f"R{index}",
                    "relationship": request.get("relationship"),
                }
                for index, request in enumerate(requests, start=1)
            ],
        )
        _trace_stage(
            process_trace,
            "Planner / SSS / Retrieval",
            "skipped",
            "Router clarification хүссэн тул дараагийн шатнууд ажиллаагүй.",
        )
        return _with_process_trace(
            _result(
                "Хүсэлтүүдийг найдвартай ялгаж чадсангүй. Асуултаа тодруулж эсвэл тус тусад нь илгээнэ үү.",
                "clarification",
            ),
            process_trace,
        )

    if any(
        not isinstance(request.get("resolved_question"), str)
        or not request["resolved_question"].strip()
        for request in requests
    ):
        _trace_stage(
            process_trace,
            "Router validation",
            "failed",
            "Router хоосон resolved_question буцаасан.",
        )
        return _with_process_trace(
            _result(
                "Асуултын агуулгыг тодруулж чадсангүй. Асуултаа дэлгэрүүлнэ үү.",
                "clarification",
            ),
            process_trace,
        )

    _trace_stage(
        process_trace,
        "Router",
        "completed",
        "Асуултыг request болгон ангилж, хайлтын асуулт/resolved_question үүсгэв.",
        details=[
            {
                "router_position": index,
                "intent": request["intent"],
                "relationship": request["relationship"],
                "resolved_question": request["resolved_question"],
                "search_query": request.get("search_query", ""),
                "related_turn_ids": request.get("related_turn_ids", []),
            }
            for index, request in enumerate(requests, start=1)
        ],
    )

    router_request_count = len(requests)
    greeting_count = sum(request["intent"] == "greeting" for request in requests)
    greeting_preamble = greeting_count > 0 and any(
        request["intent"] != "greeting" for request in requests
    )
    if greeting_preamble:
        requests = [request for request in requests if request["intent"] != "greeting"]
        _trace_stage(
            process_trace,
            "Greeting handling",
            "completed",
            "Холимог мессежийн greeting-ийг нэг эхний мэндчилгээ болгож, агуулгын request limit-ээс салгав.",
            details={
                "router_request_count": router_request_count,
                "greeting_request_count": greeting_count,
                "substantive_request_count": len(requests),
            },
        )

    if (
        len(requests) > MAX_ROUTED_REQUESTS
        or greeting_count > 1
        or router_request_count > MAX_ROUTED_REQUESTS + 1
    ):
        _trace_stage(
            process_trace,
            "Request limit",
            "failed",
            "Агуулгын request эсвэл greeting-ийн зөвшөөрсөн тооны хязгаар давсан тул үргэлжлүүлээгүй.",
            details={
                "router_request_count": router_request_count,
                "greeting_request_count": greeting_count,
                "substantive_request_count": len(requests),
                "maximum_substantive_requests": MAX_ROUTED_REQUESTS,
                "maximum_greeting_requests": 1,
            },
        )
        answer = (
            "Нэг зурваст гурваас олон агуулгын хүсэлт эсвэл нэгээс олон greeting "
            "байна. Тус тусад нь илгээнэ үү."
        )
        if greeting_preamble:
            answer = f"{_GREETING_RESPONSE}\n\n{answer}"
        return _with_process_trace(_result(answer, "clarification"), process_trace)

    stage_being_processed = "Deterministic policy check"
    try:
        expected_request_ids = {
            f"R{index}"
            for index, request in enumerate(requests, start=1)
            if get_policy_and_route(request["intent"])["route"] == "retrieval"
        }
        policy_outcomes = []
        for index, request in enumerate(requests, start=1):
            policy_route = get_policy_and_route(request["intent"])
            policy_outcomes.append(
                {
                    "request_id": f"R{index}",
                    "intent": request["intent"],
                    **policy_route,
                    "retrieval_allowed": f"R{index}" in expected_request_ids,
                }
            )
        _trace_stage(
            process_trace,
            "Deterministic policy check",
            "completed",
            "Python policy/route шалгалтын дараа зөвхөн зөвшөөрөгдсөн request-үүдийг planning-д үлдээв.",
            details=policy_outcomes,
        )
        stage_being_processed = "Planner"
        proposals = plan_requests(requests, model=model, api_key=api_key)
        if {proposal.get("request_id") for proposal in proposals} != expected_request_ids:
            raise ValueError("Planner did not return every allowed retrieval request")
        _trace_stage(
            process_trace,
            "Planner",
            "completed" if proposals else "skipped",
            (
                f"{len(proposals)} retrieval request-д нэг batch Planner дуудалт хийв."
                if proposals
                else "Зөвшөөрөгдсөн retrieval request байхгүй тул Planner model call хийгээгүй."
            ),
            details=proposals,
        )

        resolved_questions = {
            f"R{index}": request["resolved_question"]
            for index, request in enumerate(requests, start=1)
            if f"R{index}" in expected_request_ids
        }
        proposals_by_id = {proposal["request_id"]: proposal for proposal in proposals}
        search_queries = {}
        for index, request in enumerate(requests, start=1):
            request_id = f"R{index}"
            if request_id not in expected_request_ids:
                continue
            query = request.get("search_query")
            search_queries[request_id] = (
                query
                if isinstance(query, str) and query.strip()
                else request["resolved_question"]
            )
        # Reserve the existing per-request maximum: two planned queries plus one retry.
        search_budget = {"remaining": 3 * len(proposals)}
        stage_being_processed = "Select Search Strategy"
        plans = select_search_plans(
            proposals,
            resolved_questions,
            remaining_search_budget=search_budget["remaining"],
            search_queries=search_queries,
        )
        if [item.get("request_id") for item in plans] != [
            proposal["request_id"] for proposal in proposals
        ]:
            raise ValueError("Selector did not return one outcome per proposal")
        _trace_stage(
            process_trace,
            "Select Search Strategy",
            "completed" if plans else "skipped",
            "SSS бүх proposal-д deterministic SearchPlan эсвэл limitation буцаав.",
            details=[
                {
                    "request_id": item.get("request_id"),
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                    "search_plan": item.get("search_plan"),
                }
                for item in plans
            ],
        )
    except Exception as error:
        _trace_stage(
            process_trace,
            stage_being_processed,
            "failed",
            f"{stage_being_processed} үе шатны validation амжилтгүй.",
            details={"error_type": type(error).__name__},
        )
        return _with_process_trace(
            _result("Хайлтын төлөвлөгөөг аюулгүй бэлдэж чадсангүй.", "error"),
            process_trace,
        )

    stage_outcomes = {}
    search = retrieval_provider if retrieval_provider is not None else search_documents
    for plan_outcome in plans:
        request_id = plan_outcome["request_id"]
        if plan_outcome.get("status") != "planned":
            stage_outcomes[request_id] = plan_outcome
            continue

        def search_planned_query(query: str, limit: int) -> list[dict]:
            return _search_passages(
                query,
                search,
                eligible_ids,
                limit=limit,
                search_budget=search_budget,
            )

        try:
            retrieved = execute_search_plan(
                plan_outcome["search_plan"], search_provider=search_planned_query
            )
            stage_outcomes[request_id] = {
                "status": "planned",
                "search_plan": plan_outcome["search_plan"],
                "retrieval": retrieved,
            }
        except Exception as error:
            stage_outcomes[request_id] = {
                "status": "retrieval_error",
                "error_type": type(error).__name__,
            }

    retrieval_trace = []
    for plan_outcome in plans:
        request_id = plan_outcome["request_id"]
        outcome = stage_outcomes[request_id]
        retrieval = outcome.get("retrieval")
        retrieval_trace.append(
            {
                "request_id": request_id,
                "status": (
                    retrieval.get("status")
                    if isinstance(retrieval, dict)
                    else outcome.get("status")
                ),
                "reason": outcome.get("reason"),
                "error_type": outcome.get("error_type"),
                "searches": retrieval.get("searches", [])
                if isinstance(retrieval, dict)
                else [],
                "passage_count": len(retrieval.get("passages", []))
                if isinstance(retrieval, dict)
                else 0,
            }
        )
    retrieval_statuses = {item["status"] for item in retrieval_trace}
    if not retrieval_trace:
        retrieval_trace_status = "skipped"
        retrieval_summary = "Зөвшөөрөгдсөн SearchPlan байхгүй тул provider хайлт хийгээгүй."
    elif "error" in retrieval_statuses or "retrieval_error" in retrieval_statuses:
        retrieval_trace_status = "failed" if retrieval_statuses <= {"error", "retrieval_error"} else "partial"
        retrieval_summary = "Нэг буюу хэд хэдэн хайлт provider error буцаав."
    elif "partial_error" in retrieval_statuses:
        retrieval_trace_status = "partial"
        retrieval_summary = "Зарим query амжилттай, зарим нь алдаатай/хязгаарлагдсан."
    elif any(status != "success" and status != "empty" for status in retrieval_statuses):
        retrieval_trace_status = "partial"
        retrieval_summary = "Зарим request дээр SearchPlan хэрэгжээгүй."
    else:
        retrieval_trace_status = "completed"
        retrieval_summary = "SearchPlan-ийн query бүрийг provider руу ажиллуулав."
    _trace_stage(
        process_trace,
        "Retrieval",
        retrieval_trace_status,
        retrieval_summary,
        details={
            "requests": retrieval_trace,
            "remaining_search_budget_after_initial_retrieval": search_budget["remaining"],
        },
    )

    if len(requests) == 1:
        result = _process_request(
            requests[0],
            catch_errors=False,
            previous_passages=_selected_turn_passages(
                prepared_history,
                requests[0].get("related_turn_ids", []),
                eligible_ids,
            ),
            retrieval_provider=retrieval_provider,
            eligible_file_ids=eligible_ids,
            model=model,
            api_key=api_key,
            planning_proposal=proposals_by_id.get("R1"),
            stage_outcome=stage_outcomes.get("R1"),
            search_budget=search_budget,
            process_trace=process_trace,
            request_id="R1",
        )
        if greeting_preamble:
            result["answer"] = f"{_GREETING_RESPONSE}\n\n{result['answer']}"
        return _with_process_trace(result, process_trace)

    results = []
    for index, request in enumerate(requests, start=1):
        request_id = f"R{index}"
        result = _process_request(
            request,
            catch_errors=True,
            previous_passages=_selected_turn_passages(
                prepared_history,
                request.get("related_turn_ids", []),
                eligible_ids,
            ),
            retrieval_provider=retrieval_provider,
            eligible_file_ids=eligible_ids,
            model=model,
            api_key=api_key,
            planning_proposal=proposals_by_id.get(request_id),
            stage_outcome=stage_outcomes.get(request_id),
            search_budget=search_budget,
            process_trace=process_trace,
            request_id=request_id,
        )
        result.update(
            request_id=request_id,
            question=request["resolved_question"],
        )
        results.append(result)

    results = renumber_multi_request_citations(results)
    for result in results:
        request_id = result["request_id"]
        result["citations"] = [
            {
                **citation,
                "source_id": f"{request_id}-{citation['source_id']}",
            }
            for citation in result["citations"]
        ]

    answer = "\n\n".join(
        f"{result['question']}\n{result['answer']}" for result in results
    )
    result = {
        "answer": f"{_GREETING_RESPONSE}\n\n{answer}" if greeting_preamble else answer,
        "citations": [citation for result in results for citation in result["citations"]],
        "status": "multi",
        "passages": [
            {**passage, "request_id": result["request_id"]}
            for result in results
            for passage in result["passages"]
        ],
        "evidence": [
            {**passage, "request_id": result["request_id"]}
            for result in results
            for passage in result.get("evidence", [])
        ],
        "results": results,
        "process_trace": process_trace,
    }
    if greeting_preamble:
        result["greeting"] = _GREETING_RESPONSE
    return result
