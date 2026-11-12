from rag.generation import generate_answer
from rag.retrieval import search_documents
from rag.routing import (
    INTENT_LABELS,
    classify_intent,
    get_enabled_restriction,
)


def answer_question(question: str) -> dict:
    try:
        routing = classify_intent(question)
        intent = routing["intent"]
        search_query = routing.get("search_query")
        restriction = get_enabled_restriction(intent)
    except Exception:
        intent = None
        restriction = None

    if intent not in INTENT_LABELS:
        return {
            "answer": "Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.",
            "citations": [],
            "status": "error",
            "passages": [],
        }
    if restriction:
        return {
            "answer": restriction["response"],
            "citations": [],
            "status": "restricted",
            "passages": [],
        }

    non_search_responses = {
        "general": (
            "out_of_scope",
            "Энэ туслах зөвхөн ажлын байрны бодлого, HR болон үйл ажиллагааны сэдэвт тусална.",
        ),
        "greeting": (
            "greeting",
            "Сайн байна уу! Ажлын байрны бодлого, HR эсвэл үйл ажиллагааны талаар асуугаарай.",
        ),
    }
    if intent in non_search_responses:
        status, answer = non_search_responses[intent]
        return {"answer": answer, "citations": [], "status": status, "passages": []}

    if not isinstance(search_query, str) or not search_query.strip():
        search_query = question

    passages = search_documents(search_query, limit=5)
    return {**generate_answer(question, passages), "passages": passages}
