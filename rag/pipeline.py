from rag.generation import generate_answer
from rag.retrieval import search_documents


def answer_question(question: str) -> dict:
    passages = search_documents(question, limit=5)
    return {**generate_answer(question, passages), "passages": passages}
