from rag.generation import generate_answer
from rag.retrieval import search_documents
from rag.routing import (
    INTENT_LABELS,
    RELATIONSHIP_LABELS,
    classify_intent,
    get_enabled_restriction,
)


def answer_question(question: str, history: list[dict] | None = None) -> dict:
    try:
        routing = classify_intent(question, history)
        intent = routing["intent"]
        relationship = routing["relationship"]
        resolved_question = routing.get("resolved_question")
        search_query = routing.get("search_query")
        restriction = get_enabled_restriction(intent)
    except Exception:
        intent = None
        relationship = None
        resolved_question = None
        search_query = None
        restriction = None

    if intent not in INTENT_LABELS or relationship not in RELATIONSHIP_LABELS:
        return {
            "answer": "Асуултыг аюулгүй ангилж чадсангүй. Дахин оролдоно уу.",
            "citations": [],
            "status": "error",
            "passages": [],
        }

    if relationship == "clarification":
        return {
            "answer": "Асуултын сэдэв эсвэл өмнөх яриатай холбоог тодруулж өгнө үү.",
            "citations": [],
            "status": "clarification",
            "passages": [],
        }
    if restriction:
        return {
            "answer": restriction["response"],
            "citations": [],
            "status": "restricted",
            "passages": [],
        }

    effective_question = question if relationship == "new" else resolved_question
    if not isinstance(effective_question, str) or not effective_question.strip():
        return {
            "answer": (
                "Өмнөх ярианаас энэ асуултыг аюулгүй тодруулж чадсангүй. "
                "Асуултаа дэлгэрүүлнэ үү."
            ),
            "citations": [],
            "status": "clarification",
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
        search_query = effective_question

    passages = search_documents(search_query, limit=5)
    return {**generate_answer(effective_question, passages), "passages": passages}
