from rag.generation import generate_answer, review_evidence
from rag.retrieval import (
    build_search_query,
    deduplicate_passages,
    previous_turn_passages,
    search_documents,
)
from rag.routing import (
    INTENT_LABELS,
    MAX_ROUTED_REQUESTS,
    RELATIONSHIP_LABELS,
    classify_intent,
    get_enabled_restriction,
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
    restriction = get_enabled_restriction(intent)
    if restriction:
        return _result(restriction["response"], "restricted")

    effective_question = request["resolved_question"]
    responses = {
        "general": (
            "out_of_scope",
            "Энэ туслах зөвхөн ажлын байрны бодлого, HR болон үйл ажиллагааны сэдэвт тусална.",
        ),
        "greeting": (
            "greeting",
            "Сайн байна уу! Ажлын байрны бодлого, HR эсвэл үйл ажиллагааны талаар асуугаарай.",
        ),
    }
    if intent in responses:
        status, answer = responses[intent]
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

    previous_passages = previous_turn_passages(history, eligible_ids)
    if len(requests) == 1:
        return _process_request(
            requests[0],
            catch_errors=False,
            previous_passages=previous_passages,
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
            previous_passages=previous_passages,
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
