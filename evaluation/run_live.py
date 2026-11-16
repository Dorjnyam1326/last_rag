"""Opt-in, resumable live evaluation. Detailed output remains git-ignored."""

import argparse
import csv
import hashlib
import json
import os
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.callbacks import get_usage_metadata_callback


ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "evaluation" / "cases.json"
TRACE_FILE = ROOT / "evaluation" / "live_results.json"
REVIEW_FILE = ROOT / "evaluation" / "human_review.csv"
SMOKE_IDS = ("C01", "C08", "C14", "C17", "C18", "C21", "C25", "C30")
MAX_OUTPUT_TOKENS = 1200
MAX_MODEL_CALLS_PER_CASE = 11  # 1 router + 1 planner + up to 3 requests * (2 reviews + 1 answer)
MODEL_CONTEXT_TOKENS = 1_050_000
STOP_USD = Decimal("15")
REGIONAL_UPLIFT = Decimal("1.10")


def _worst_call_cost() -> Decimal:
    # Conservative high-context Standard rates, plus the possible regional uplift.
    input_cost = Decimal(MODEL_CONTEXT_TOKENS) * Decimal("0.20") / 1_000_000
    output_cost = Decimal(MAX_OUTPUT_TOKENS) * Decimal("0.75") / 1_000_000
    return (input_cost + output_cost) * REGIONAL_UPLIFT


MAX_CASE_COST = _worst_call_cost() * MAX_MODEL_CALLS_PER_CASE
CALL_KEYS = ("router", "planner", "reviewer", "generation", "vector_store_search")


def _add_call_counts(total: dict, addition: dict) -> dict:
    return {key: total.get(key, 0) + addition.get(key, 0) for key in CALL_KEYS}


def _usage_cost(usage: dict) -> tuple[dict, Decimal]:
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    long_context = input_tokens > 272_000
    input_rate = Decimal("0.20" if long_context else "0.10")
    output_rate = Decimal("0.75" if long_context else "0.50")
    estimate = (
        Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
    ) / 1_000_000 * REGIONAL_UPLIFT
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens", input_tokens + output_tokens)),
    }, estimate


def _load_question_set(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Question set must be a non-empty JSON array.")

    cases = []
    seen_ids = set()
    session_turns = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every question-set row must be an object.")
        case_id = row.get("id")
        session_id = row.get("session_id")
        turn_index = row.get("turn_index")
        language = row.get("language")
        query = row.get("query")
        labels = row.get("expected_intents")
        follow_up = row.get("follow_up")
        if (
            case_id is None or str(case_id) in seen_ids
            or not isinstance(session_id, str) or not session_id.strip()
            or not isinstance(language, str) or not language.strip()
            or not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 1
            or not isinstance(query, str) or not query.strip()
            or not isinstance(labels, list) or not labels
            or any(not isinstance(label, str) or not label.strip() for label in labels)
            or not isinstance(follow_up, bool)
        ):
            raise ValueError("Question set has a missing, duplicate, or invalid field.")
        prior_turn = session_turns.get(session_id, 0)
        if turn_index != prior_turn + 1 or follow_up != (turn_index > 1):
            raise ValueError(f"Invalid turn order or follow-up label in session {session_id!r}.")
        seen_ids.add(str(case_id))
        session_turns[session_id] = turn_index
        cases.append({
            "id": f"LQ{int(case_id):02d}" if str(case_id).isdigit() else f"LQ-{case_id}",
            "split": "external_unlabeled",
            "question": query,
            "history": [],
            "language": language,
            "expected": [
                {
                    "intent": None,
                    "relationship": "follow_up" if follow_up else "new",
                    "action": "search",
                }
                for _ in labels
            ],
            "expected_intent_labels": labels,
            "session_id": session_id,
            "turn_index": turn_index,
            "follow_up": follow_up,
            "source_verified": False,
        })
    return cases, hashlib.sha256(raw).hexdigest()


def _append_session_history(histories: dict, case: dict, answer: str) -> None:
    session_id = case.get("session_id")
    if not session_id:
        return
    histories.setdefault(session_id, []).extend((
        {"role": "user", "content": case["question"]},
        {"role": "assistant", "content": answer},
    ))


def _output_paths(question_set: Path | None) -> tuple[Path, Path]:
    if question_set is None:
        return TRACE_FILE, REVIEW_FILE
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", question_set.stem).strip("_") or "external"
    output_dir = ROOT / "evaluation" / "runs"
    return (
        output_dir / f"{stem}_live_results.json",
        output_dir / f"{stem}_human_review.csv",
    )


def _text_evidence(passages: list[dict]) -> bool:
    return any(isinstance(p.get("text"), str) and p["text"].strip() for p in passages)


def _safe_request(request: dict) -> dict:
    return {
        key: request.get(key)
        for key in ("intent", "relationship", "search_query", "resolved_question")
    }


def _safe_result(result: dict) -> dict:
    fields = (
        "answer", "citations", "status", "request_id", "question",
        "retrieval_status", "planning_status", "retry_error_type",
    )
    clean = {key: result[key] for key in fields if key in result}
    if result.get("results"):
        clean["results"] = [
            {key: item[key] for key in fields if key in item}
            for item in result["results"]
        ]
    return clean


def _safe_error_type(result: dict) -> str | None:
    error_type = result.get("error_type")
    return error_type if isinstance(error_type, str) and error_type.isidentifier() else None


def _check_case(case: dict, route: dict, result: dict, trace: dict) -> dict:
    expected = case["expected"]
    routed = route.get("requests", []) if route else []
    grouped = result.get("results") or [result]
    route_count_ok = len(routed) == len(expected)
    route_items_ok = route_count_ok and all(
        (want.get("relationship") is None or item.get("relationship") == want["relationship"])
        and (
            want.get("relationship") == "clarification"
            or want.get("intent") is None
            or item.get("intent") == want["intent"]
        )
        for item, want in zip(routed, expected)
    )
    action_checks = []
    for index, want in enumerate(expected, start=1):
        rid = f"R{index}"
        item = grouped[index - 1] if index <= len(grouped) else {}
        searches = sum(c.get("request_id") == rid for c in trace["searches"])
        generations = sum(
            c.get("request_id") == rid and c.get("llm_attempted")
            for c in trace["generations"]
        )
        action = want["action"]
        if action == "search":
            ok = searches > 0 and item.get("status") not in {
                "restricted", "clarification", "greeting", "out_of_scope"
            }
        elif action == "restricted":
            ok = item.get("status") == "restricted" and searches == 0 and generations == 0
        elif action == "clarification":
            ok = item.get("status") == "clarification" and searches == 0 and generations == 0
        elif action == "greeting":
            ok = item.get("status") == "greeting" and searches == 0 and generations == 0
        else:  # out_of_scope
            ok = item.get("status") == "out_of_scope" and searches == 0 and generations == 0
        action_checks.append({"request_id": rid, "expected": action, "passed": ok})
    taxonomy_applicable = any(
        want.get("intent") is not None or want.get("relationship") is not None
        for want in expected
    )
    return {
        "request_count_match": route_count_ok,
        "intent_relationship_match": route_items_ok if taxonomy_applicable else None,
        "actions": action_checks,
        "passed": route_count_ok and route_items_ok and all(c["passed"] for c in action_checks),
    }


def _write_review_sheet(cases: list[dict], results: dict, review_file: Path = REVIEW_FILE) -> None:
    review_file.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id", "split", "question", "status", "answer", "citations",
        "policy_correct", "evidence_supports_claims", "citations_support_claims",
        "unsupported_claims", "reviewer_notes",
    ]
    if any(case.get("session_id") for case in cases):
        fields[2:2] = ["session_id", "turn_index", "language", "expected_intent_labels"]
    with review_file.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        for case in cases:
            item = results.get(case["id"])
            if item:
                row = {
                    "case_id": case["id"],
                    "split": case["split"],
                    "session_id": case.get("session_id", ""),
                    "turn_index": case.get("turn_index", ""),
                    "language": case.get("language", ""),
                    "expected_intent_labels": json.dumps(
                        case.get("expected_intent_labels", []), ensure_ascii=False
                    ),
                    "question": case["question"],
                    "status": item["result"].get("status"),
                    "answer": item["result"].get("answer"),
                    "citations": json.dumps(item["result"].get("citations", []), ensure_ascii=False),
                    "policy_correct": "",
                    "evidence_supports_claims": "",
                    "citations_support_claims": "",
                    "unsupported_claims": "",
                    "reviewer_notes": "",
                }
                if not case.get("session_id"):
                    for field in ("session_id", "turn_index", "language", "expected_intent_labels"):
                        row.pop(field)
                writer.writerow(row)


def _save(
    state: dict,
    cases: list[dict],
    trace_file: Path = TRACE_FILE,
    review_file: Path = REVIEW_FILE,
) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat()
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    temp = trace_file.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(trace_file)
    _write_review_sheet(cases, state["cases"], review_file)


def _refresh_metrics(state: dict, by_id: dict) -> None:
    for case_id, trace in state["cases"].items():
        if case_id in by_id:
            trace["objective_checks"] = _check_case(
                by_id[case_id], {"requests": trace.get("route") or []}, trace["result"], trace
            )
    expected = {case_id: by_id[case_id] for case_id in state["cases"] if case_id in by_id}
    intent_total = intent_matches = relationship_matches = relationship_total = 0
    for case_id, case in expected.items():
        routed = state["cases"][case_id].get("route") or []
        for want, actual in zip(case["expected"], routed):
            if want.get("relationship") is not None:
                relationship_total += 1
                relationship_matches += actual.get("relationship") == want["relationship"]
            if want.get("intent") is not None and want.get("relationship") != "clarification":
                intent_total += 1
                intent_matches += actual.get("intent") == want["intent"]
    action_checks = [
        action
        for trace in state["cases"].values()
        for action in trace["objective_checks"]["actions"]
    ]
    review_statuses = [
        review.get("review_status", review.get("status")) or "unrecorded"
        for trace in state["cases"].values()
        for review in trace["reviews"]
    ]
    result_statuses = [
        result.get("status")
        for trace in state["cases"].values()
        for result in (trace["result"].get("results") or [trace["result"]])
    ]
    retries = sum(
        search.get("attempt") == "retry"
        for trace in state.get("runs", state["cases"].values())
        for search in trace["searches"]
    )
    usage_records = []
    for run in state.get("runs", state["cases"].values()):
        router_usage = run.get("router_call", {}).get("usage") or {}
        if router_usage.get("usage_available"):
            usage_records.append(run["router_call"]["usage"])
        usage_records.extend(
            item["usage"]
            for item in run.get("planners", [])
            if (item.get("usage") or {}).get("usage_available")
        )
        for group in ("reviews", "generations"):
            usage_records.extend(
                item["usage"] for item in run.get(group, [])
                if (item.get("usage") or {}).get("usage_available")
            )
    model_call_count = sum(
        state.get("calls", {}).get(key, 0)
        for key in ("router", "planner", "reviewer", "generation")
    )
    errors = sum(
        sum(result.get("status") == "error" for result in (run["result"].get("results") or [run["result"]]))
        + sum(
            "error_type" in item
            for group in ("planners", "retrievals", "searches", "reviews", "generations")
            for item in run.get(group, [])
        )
        for run in state.get("runs", state["cases"].values())
    )
    state["metrics"] = {
        "completed_cases": len(state["cases"]),
        "case_attempts": len(state.get("runs", [])) + len(state.get("accounting_adjustments", [])),
        "holdout_completed": sum(trace["split"] == "holdout" for trace in state["cases"].values()),
        "request_count_matches": sum(trace["objective_checks"]["request_count_match"] for trace in state["cases"].values()),
        "intent_accuracy": f"{intent_matches}/{intent_total}",
        "relationship_matches": f"{relationship_matches}/{relationship_total}",
        "expected_action_matches": f"{sum(a['passed'] for a in action_checks)}/{len(action_checks)}",
        "multi_intent_cases": sum(len(case["expected"]) > 1 for case in expected.values()),
        "multi_intent_cases_with_correct_request_count": sum(
            len(case["expected"]) > 1 and state["cases"][case_id]["objective_checks"]["request_count_match"]
            for case_id, case in expected.items()
        ),
        "follow_up_cases": sum(
            bool(case.get("follow_up")) or any(
                request.get("relationship") == "follow_up" for request in case["expected"]
            )
            for case in expected.values()
        ),
        "follow_up_relationship_matches": sum(
            actual.get("relationship") == "follow_up"
            for trace in state["cases"].values() for actual in (trace.get("route") or [])
        ),
        "review_status_counts": {status: review_statuses.count(status) for status in sorted(set(review_statuses))},
        "final_status_counts": {status: result_statuses.count(status) for status in sorted(set(result_statuses))},
        "retry_search_count": retries,
        "source_verified_cases": sum(bool(case["source_verified"]) for case in expected.values()),
        "api_usage": {
            "usage_records_available": len(usage_records),
            "model_calls": model_call_count,
            "usage_records_missing": model_call_count - len(usage_records),
            "input_tokens_recorded": sum(item.get("input_tokens", 0) for item in usage_records),
            "output_tokens_recorded": sum(item.get("output_tokens", 0) for item in usage_records),
            "total_tokens_recorded": sum(item.get("total_tokens", 0) for item in usage_records),
            "complete": model_call_count == len(usage_records),
        },
        "error_count": errors,
        "human_answer_quality": "pending human review; no verified answer labels",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true", help="Required to make paid API calls")
    parser.add_argument("--question-set", type=Path, help="Run an external question JSON array as one session-aware suite")
    parser.add_argument("--all-cases", action="store_true", help="Run all 30 cases, including holdout")
    parser.add_argument("--case-ids", nargs="+", help="Run selected cases; default is the eight-case smoke set")
    parser.add_argument("--rerun-cases", nargs="+", help="Rerun only these selected cases")
    args = parser.parse_args()
    if args.question_set and (args.all_cases or args.case_ids or args.rerun_cases):
        parser.error("--question-set runs its complete dataset; do not combine it with case selection flags")
    if args.all_cases and args.case_ids:
        parser.error("Use either --all-cases or --case-ids, not both")
    if not args.confirm_live:
        print("No API calls made. Add --confirm-live to opt in.")
        return 2

    load_dotenv(ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", "gpt-6-luna")
    if (
        model != "gpt-6-luna"
        or not os.getenv("OPENAI_API_KEY")
        or not os.getenv("OPENAI_VECTOR_STORE_ID")
        or os.getenv("OPENAI_BASE_URL")
    ):
        print("Live run stopped: configured model/provider or required credentials do not match the verified pricing scope.")
        return 2

    external_digest = None
    if args.question_set:
        try:
            cases, external_digest = _load_question_set(args.question_set)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"Question set validation failed: {type(exc).__name__}: {exc}")
            return 2
    else:
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    trace_file, review_file = _output_paths(args.question_set)
    by_id = {case["id"]: case for case in cases}
    if args.question_set or args.all_cases:
        selected = cases
    else:
        ids = args.case_ids or list(SMOKE_IDS)
        if any(case_id not in by_id for case_id in ids):
            parser.error("Unknown evaluation case ID")
        selected = [by_id[case_id] for case_id in ids]
    rerun_ids = set(args.rerun_cases or [])
    if not rerun_ids.issubset({case["id"] for case in selected}):
        parser.error("--rerun-cases must be included in --case-ids")
    if args.all_cases:
        selected = sorted(selected, key=lambda case: (case["split"] == "holdout", case["id"]))

    if trace_file.exists():
        state = json.loads(trace_file.read_text(encoding="utf-8"))
        if state.get("model") != model or state.get("service_tier") != "default":
            print("Live run stopped: existing trace uses a different model or processing tier.")
            return 2
        if state.get("question_set_sha256") != external_digest:
            print("Live run stopped: output trace belongs to a different question set.")
            return 2
        state.setdefault("runs", list(state.get("cases", {}).values()))
        state.setdefault("accounting_adjustments", [])
    else:
        state = {
            "model": model,
            "service_tier": "default",
            "rates_usd_per_million": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
            "cost_estimate_includes_regional_uplift_percent": 10,
            "max_completion_tokens_per_model_call": MAX_OUTPUT_TOKENS,
            "request_timeout_seconds": 45,
            "max_retries": 0,
            "calls": {key: 0 for key in CALL_KEYS},
            "estimated_cost_upper_bound_usd": "0",
            "cases": {},
        }
        if external_digest:
            state["question_set_name"] = args.question_set.name
            state["question_set_sha256"] = external_digest
            state["question_count"] = len(cases)
            state["answer_quality"] = "unverified; input dataset has intent labels only"

    remaining_cost = Decimal(state["estimated_cost_upper_bound_usd"])
    pending = [
        case for case in selected
        if case["id"] not in state["cases"] or case["id"] in rerun_ids
    ]
    if not pending:
        _refresh_metrics(state, by_id)
        _save(state, cases, trace_file, review_file)
        print("All selected cases already have results; no API calls made.")
        return 0

    from rag import pipeline
    from rag.pipeline import answer_question

    total = len(pending)
    session_histories = {}
    pending_ids = {case["id"] for case in pending}
    completed_index = 0
    for case in selected:
        is_external = bool(case.get("session_id"))
        history = list(session_histories.get(case["session_id"], [])) if is_external else case["history"]
        if case["id"] not in pending_ids:
            _append_session_history(
                session_histories, case, state["cases"][case["id"]]["result"].get("answer", "")
            )
            continue
        completed_index += 1
        if remaining_cost + MAX_CASE_COST > STOP_USD:
            print("Stopped before next case: conservative estimate would cross $15.")
            break

        trace = {
            "case_id": case["id"], "split": case["split"], "question": case["question"],
            "history": history, "route": None, "searches": [], "reviews": [],
            "planners": [], "retrievals": [], "generations": [],
            "calls": {key: 0 for key in CALL_KEYS},
            "estimated_cost_upper_bound_usd": "0",
        }
        if is_external:
            trace.update({
                "session_id": case["session_id"],
                "turn_index": case["turn_index"],
                "language": case["language"],
                "expected_intent_labels": case["expected_intent_labels"],
            })
        request_index = 0
        active_trace = trace
        active_request: ContextVar[str] = ContextVar("evaluation_request_id", default="R0")
        planned_queries: dict[str, set[str]] = {}
        cost_for_case = Decimal("0")

        def record_usage(component: str, callback, attempted: bool) -> tuple[dict | None, Decimal]:
            nonlocal cost_for_case, remaining_cost
            usage_by_model = callback.usage_metadata
            if usage_by_model:
                usage = next(iter(usage_by_model.values()))
                token_record, cost = _usage_cost(usage)
                entry = {"model": next(iter(usage_by_model)), **token_record, "usage_available": True}
            elif attempted:
                cost = _worst_call_cost()
                entry = {"model": model, "usage_available": False, "estimated_at_max_context": True}
            else:
                return None, Decimal("0")
            cost_for_case += cost
            remaining_cost += cost
            trace["calls"][component] += 1
            return entry, cost

        original_router = pipeline.classify_intent
        original_planner = pipeline.plan_requests
        original_execute = pipeline.execute_search_plan
        original_search = pipeline.search_documents
        original_review = pipeline.review_evidence
        original_generation = pipeline.generate_answer
        original_process = pipeline._process_request

        def tracked_router(question, history=None, *, model=None, api_key=None):
            started = time.perf_counter()
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_router(
                        question, history, model=model, api_key=api_key
                    )
                    trace["route"] = [_safe_request(r) for r in value.get("requests", [])]
                    return value
                finally:
                    usage, cost = record_usage("router", callback, attempted=True)
                    trace.setdefault("router_call", {}).update({
                        "latency_seconds": round(time.perf_counter() - started, 3),
                        "usage": usage, "estimated_cost_usd": str(cost),
                    })

        def tracked_planner(requests, *, model=None, api_key=None):
            started = time.perf_counter()
            attempted = any(
                pipeline.get_policy_and_route(request["intent"])["route"] == "retrieval"
                for request in requests
            )
            record = {"request_ids": [], "proposal_counts": []}
            with get_usage_metadata_callback() as callback:
                try:
                    proposals = original_planner(
                        requests, model=model, api_key=api_key
                    )
                    record["request_ids"] = [item["request_id"] for item in proposals]
                    record["proposal_counts"] = [
                        {
                            "required_facts": len(item["required_facts"]),
                            "information_needs": len(item["information_needs"]),
                            "required_capabilities": item["required_capabilities"],
                            "preferred_capabilities": item["preferred_capabilities"],
                        }
                        for item in proposals
                    ]
                    return proposals
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    raise
                finally:
                    usage, cost = record_usage("planner", callback, attempted)
                    record.update({
                        "usage": usage,
                        "estimated_cost_usd": str(cost),
                        "latency_seconds": round(time.perf_counter() - started, 3),
                    })
                    trace["planners"].append(record)

        def tracked_search(query, limit=5):
            started = time.perf_counter()
            request_id = active_request.get()
            record = {
                "request_id": request_id,
                "attempt": "initial" if query in planned_queries.get(request_id, set()) else "retry",
                "query": query,
                "limit": limit,
            }
            trace["calls"]["vector_store_search"] += 1
            try:
                results = original_search(query, limit)
                record["results"] = [
                    {"file_id": item.get("file_id"), "score": item.get("score")}
                    for item in results
                ]
                return results
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["latency_seconds"] = round(time.perf_counter() - started, 3)
                trace["searches"].append(record)

        def tracked_execute_search_plan(search_plan, *, search_provider=None):
            request_id = search_plan["request_id"]
            token = active_request.set(request_id)
            planned_queries[request_id] = {
                query["text"] for query in search_plan["queries"]
            }
            record = {
                "request_id": request_id,
                "strategy_id": search_plan["strategy_id"],
                "query_count": len(search_plan["queries"]),
            }
            started = time.perf_counter()
            try:
                result = original_execute(
                    search_plan, search_provider=search_provider
                )
                record.update({
                    "status": result["status"],
                    "searches": result["searches"],
                    "passage_count": len(result["passages"]),
                })
                return result
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["latency_seconds"] = round(time.perf_counter() - started, 3)
                trace["retrievals"].append(record)
                active_request.reset(token)

        def tracked_review(question, passages, *args, **kwargs):
            started = time.perf_counter()
            attempted = _text_evidence(passages)
            record = {"request_id": active_request.get(), "question": question}
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_review(question, passages, *args, **kwargs)
                    record.update({
                        "status": value.get("review_status", value.get("status")),
                        "relevant_ids": value.get("relevant_ids", []),
                        "missing_information": value.get("missing_information", ""),
                        "retry_query": value.get("retry_query", ""),
                    })
                    if error_type := _safe_error_type(value):
                        record["error_type"] = error_type
                    return value
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    raise
                finally:
                    usage, cost = record_usage("reviewer", callback, attempted)
                    record.update({
                        "usage": usage,
                        "estimated_cost_usd": str(cost),
                        "latency_seconds": round(time.perf_counter() - started, 3),
                    })
                    trace["reviews"].append(record)

        def tracked_generation(question, passages, *args, **kwargs):
            started = time.perf_counter()
            attempted = _text_evidence(passages)
            assessment = kwargs.get("evidence_assessment") or {}
            decision = kwargs.get("evidence_decision") or {}
            record = {
                "request_id": active_request.get(), "question": question,
                "llm_attempted": attempted,
                "sources": [{"file_id": p.get("file_id"), "filename": p.get("filename")} for p in passages],
            }
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_generation(question, passages, *args, **kwargs)
                    draft_unresolved = value.get("unresolved_fact_ids", [])
                    decision_unresolved = decision.get("unresolved_fact_ids", [])
                    record.update({
                        "status": value.get("status", value.get("outcome")),
                        "citations": value.get("citations", []),
                        "draft_block_count": len(value.get("blocks", [])),
                        "draft_reference_count": sum(
                            len(item.get("evidence_ids", []))
                            for block in value.get("blocks", [])
                            for item in block.get("items", [])
                        ),
                        "draft_unresolved_fact_count": len(draft_unresolved),
                        "draft_unresolved_matches_decision": draft_unresolved == decision_unresolved,
                        "assessment_fact_status_counts": {
                            status: sum(
                                fact.get("status") == status
                                for fact in assessment.get("facts", [])
                                if isinstance(fact, dict)
                            )
                            for status in ("supported", "partial", "missing")
                        },
                        "assessment_conflict_count": len(assessment.get("conflicts", [])),
                        "decision_sufficient": decision.get("sufficient"),
                        "decision_supported_fact_count": len(decision.get("supported_fact_ids", [])),
                        "decision_unresolved_fact_count": len(decision_unresolved),
                        "decision_reason_codes": decision.get("reason_codes", []),
                    })
                    if error_type := _safe_error_type(value):
                        record["error_type"] = error_type
                    return value
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    raise
                finally:
                    usage, cost = record_usage("generation", callback, attempted)
                    record.update({
                        "usage": usage,
                        "estimated_cost_usd": str(cost),
                        "latency_seconds": round(time.perf_counter() - started, 3),
                    })
                    trace["generations"].append(record)

        def tracked_process(request, *args, **kwargs):
            nonlocal request_index
            request_index += 1
            token = active_request.set(f"R{request_index}")
            try:
                return original_process(request, *args, **kwargs)
            finally:
                active_request.reset(token)

        pipeline.classify_intent = tracked_router
        pipeline.plan_requests = tracked_planner
        pipeline.execute_search_plan = tracked_execute_search_plan
        pipeline.search_documents = tracked_search
        pipeline.review_evidence = tracked_review
        pipeline.generate_answer = tracked_generation
        pipeline._process_request = tracked_process
        started = time.perf_counter()
        uncaught_error = None
        try:
            result = answer_question(case["question"], history=history or None)
        except Exception as exc:
            uncaught_error = type(exc).__name__
            result = {
                "answer": "Evaluation request failed; see the recorded error type.",
                "citations": [], "status": "error", "passages": [],
            }
        finally:
            pipeline.classify_intent = original_router
            pipeline.plan_requests = original_planner
            pipeline.execute_search_plan = original_execute
            pipeline.search_documents = original_search
            pipeline.review_evidence = original_review
            pipeline.generate_answer = original_generation
            pipeline._process_request = original_process
        trace.update({
            "result": _safe_result(result),
            "uncaught_error_type": uncaught_error,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "estimated_cost_upper_bound_usd": str(cost_for_case),
            "objective_checks": _check_case(case, {"requests": trace["route"] or []}, result, trace),
        })
        state["cases"][case["id"]] = trace
        state.setdefault("runs", []).append(trace)
        state["estimated_cost_upper_bound_usd"] = str(remaining_cost)
        state["calls"] = _add_call_counts(state.get("calls", {}), trace["calls"])
        _save(state, cases, trace_file, review_file)
        _append_session_history(session_histories, case, result.get("answer", ""))
        print(f"{case['id']} {case['split']}: {result.get('status')} · cost upper-bound ${cost_for_case:.6f} · {completed_index}/{total}")

    _refresh_metrics(state, by_id)
    _save(state, cases, trace_file, review_file)
    print(f"Total completed={len(state['cases'])}; estimated spend upper-bound=${remaining_cost:.6f}; stop threshold=$15.")
    print(f"Local trace: {trace_file}; human review sheet: {review_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
