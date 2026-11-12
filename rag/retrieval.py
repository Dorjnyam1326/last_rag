import os
from pathlib import Path


def search_documents(query: str, limit: int = 5) -> list[dict]:
    """Search the configured Vector Store and return matching text chunks."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty text")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

    vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID")
    if not vector_store_id:
        raise RuntimeError("OPENAI_VECTOR_STORE_ID is not configured")

    from openai import OpenAI

    response = OpenAI().vector_stores.search(
        vector_store_id=vector_store_id,
        query=query,
        max_num_results=limit,
    )

    return [
        {
            "file_id": result.file_id,
            "filename": result.filename,
            "text": "\n".join(
                part.text
                for part in getattr(result, "content", [])
                if getattr(part, "type", None) == "text"
            ),
            "score": getattr(result, "score", None),
        }
        for result in response.data
    ]
