from rag.generation import generate_answer, review_evidence
from rag.retrieval import (
    MAX_REUSABLE_PASSAGES,
    build_search_query,
    deduplicate_passages,
    previous_turn_passages,
    search_documents,
)
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


def _result(answer: str, status: str) -> dict:
    return {"answer": answer, "citations": [], "status": status, "passages": []}


def _search_passages(query: str, retrieval_provider, eligible_file_ids) -> list[dict]:
    if eligible_file_ids is not None and not eligible_file_ids:
        return []
    passages = retrieval_provider(query, limit=5)
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
) -> dict:
    intent = request["intent"]
    policy_route = get_policy_and_route(intent)
    if policy_route["policy"] == "restricted":
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
            "Сайн байна уу! Ажлын байрны бодлого, HR эсвэл үйл ажиллагааны талаар асуугаарай.",
        ),
    }
    if route in responses:
        status, answer = responses[route]
        return _result(answer, status)

    search_query = request.get("search_query")
    if not isinstance(search_query, str) or not search_query.strip():
        search_query = effective_question
    search_query = build_search_query(search_query, effective_question)

    try:
        search = (
            retrieval_provider
            if retrieval_provider is not None
            else search_documents
        )
        passages = _search_passages(search_query, search, eligible_file_ids)
        if request["relationship"] == "follow_up":
            passages = deduplicate_passages([*passages, *(previous_passages or [])])
        review = review_evidence(effective_question, passages, model=model, api_key=api_key)
        retry_query = review.get("retry_query", "")
        if isinstance(retry_query, str):
            retry_query = build_search_query(retry_query, effective_question)
        if (
            review["status"] in {"partial", "insufficient"}
            and isinstance(retry_query, str)
            and retry_query.strip()
            and " ".join(retry_query.casefold().split())
            != " ".join(search_query.casefold().split())
        ):
            try:
                additional = _search_passages(retry_query, search, eligible_file_ids)
            except Exception:
                additional = []
            merged_passages = deduplicate_passages([*passages, *additional])
            added = len(merged_passages) > len(passages)
            if added:
                passages = merged_passages
                retry_review = review_evidence(effective_question, passages, model=model, api_key=api_key)
                if retry_review["status"] != "error":
                    relevant_ids = list(
                        dict.fromkeys(review["relevant_ids"] + retry_review["relevant_ids"])
                    )
                    if review["status"] == "partial" and retry_review["status"] == "insufficient" and relevant_ids:
                        retry_review["status"] = "partial"
                    retry_review["relevant_ids"] = relevant_ids
                    review = retry_review

        if review["status"] == "error":
            return {
                **_result("Баримтын хүрэлцээг шалгаж чадсангүй.", "error"),
                "passages": passages,
            }

        relevant = [
            passage
            for index, passage in enumerate(passages, start=1)
            if f"E{index}" in review["relevant_ids"]
        ]
        if review["status"] == "partial":
            generated = generate_answer(
                effective_question,
                relevant,
                evidence_status="partial",
                missing_information=review["missing_information"],
                model=model,
                api_key=api_key,
            )
        else:
            generated = generate_answer(
                effective_question,
                relevant if review["status"] != "insufficient" else [],
                model=model,
                api_key=api_key,
            )
        cited_ids = {citation["file_id"] for citation in generated["citations"]}
        evidence = [passage for passage in relevant if passage.get("file_id") in cited_ids]
        return {**generated, "passages": passages, "evidence": evidence}
    except Exception:
        if not catch_errors:
            raise
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

    try:
        routing = classify_intent(question, history, model=model, api_key=api_key)
        requests = routing["requests"]
        if not isinstance(requests, list) or not requests:
            raise ValueError("router returned no requests")
    except Exception:
        return _result("Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.", "error")

    if len(requests) > MAX_ROUTED_REQUESTS:
        return _result(
            "Нэг удаад гурваас олон хүсэлт байна. Асуултуудаа тус тусад нь илгээнэ үү.",
            "clarification",
        )

    if any(
        not isinstance(request, dict)
        or request.get("intent") not in INTENT_LABELS
        or request.get("relationship") not in RELATIONSHIP_LABELS
        for request in requests
    ):
        return _result("Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.", "error")

    prepared_history = prepare_history(history)
    _, _, allowed_turn_ids = build_router_history_context(prepared_history)
    for request in requests:
        related_turn_ids = request.get("related_turn_ids", [])
        if (
            not isinstance(related_turn_ids, list)
            or any(not isinstance(turn_id, str) for turn_id in related_turn_ids)
            or len(set(related_turn_ids)) != len(related_turn_ids)
            or any(turn_id not in allowed_turn_ids for turn_id in related_turn_ids)
            or (request["relationship"] == "follow_up" and not related_turn_ids)
            or (request["relationship"] != "follow_up" and related_turn_ids)
        ):
            return _result(
                "Асуултын холбоотой өмнөх яриаг баталгаажуулж чадсангүй. Тодруулна уу.",
                "clarification",
            )

    if any(request["relationship"] == "clarification" for request in requests):
        return _result(
            "Хүсэлтүүдийг найдвартай ялгаж чадсангүй. Асуултаа тодруулж эсвэл тус тусад нь илгээнэ үү.",
            "clarification",
        )

    if any(
        not isinstance(request.get("resolved_question"), str)
        or not request["resolved_question"].strip()
        for request in requests
    ):
        return _result(
            "Асуултын агуулгыг тодруулж чадсангүй. Асуултаа дэлгэрүүлнэ үү.",
            "clarification",
        )

    if len(requests) == 1:
        return _process_request(
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
        )

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
        )
        citations = [
            {
                **citation,
                "source_id": f"{request_id}-{citation['source_id']}",
                "request_id": request_id,
            }
            for citation in result["citations"]
        ]
        result.update(
            request_id=request_id,
            question=request["resolved_question"],
            citations=citations,
        )
        results.append(result)

    return {
        "answer": "\n\n".join(
            f"{result['question']}\n{result['answer']}" for result in results
        ),
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
    }
