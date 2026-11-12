def answer_question(question: str) -> str:
    return f"Таны асуулт: {question}"
from rag.retrieval import search_documents


def answer_question(question: str) -> list[dict]:
    return search_documents(question, limit=5)
