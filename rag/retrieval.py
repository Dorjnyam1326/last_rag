import os
import re
from pathlib import Path


MAX_REUSABLE_PASSAGES = 5
_LEAVE_TERM = re.compile(r"чөлөө|leave|vacation|амралт", re.I)
_TIME_RANGE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:–|—|-|аас|ээс|хүртэл|to)\s*\d{1,2}(?::\d{2})?\b",
    re.I,
)


def build_search_query(query: str, question: str) -> str:
    """Remove a leave time range from retrieval while keeping it in the answer question."""
    if not _LEAVE_TERM.search(question) or not _TIME_RANGE.search(query):
        return query

    original_query = query
    query = _TIME_RANGE.sub(" ", query)
    query = re.sub(r"\b(?:өдрийн\s+)?хүртэл\b", " ", query, flags=re.I)
    query = re.sub(r"\s+([,;:.?])", r"\1", query)
    query = re.sub(r"[,;:]+", " ", query)
    return " ".join(query.split()).strip(" ,;:.?") or original_query


def deduplicate_passages(passages: list[dict]) -> list[dict]:
    """Remove exact duplicate text, retaining its ranked source provenance."""
    unique = {}
    for passage in passages:
        text = passage.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        existing = unique.get(text)
        if existing is None:
            unique[text] = dict(passage)
            continue

        source = {
            key: passage.get(key)
            for key in ("file_id", "filename", "score", "origin")
            if passage.get(key) is not None
        }
        sources = existing.setdefault("source_provenance", [
            {
                key: existing.get(key)
                for key in ("file_id", "filename", "score", "origin")
                if existing.get(key) is not None
            }
        ])
        if source and source not in sources:
            sources.append(source)
    return list(unique.values())


def previous_turn_passages(
    history: list[dict] | None,
    eligible_file_ids: frozenset[str] | None = None,
) -> list[dict]:
    """Return bounded document evidence from the immediately previous assistant turn."""
    if (
        not isinstance(history, list)
        or not history
        or not isinstance(history[-1], dict)
        or history[-1].get("role") != "assistant"
    ):
        return []

    passages = history[-1].get("passages", [])
    if not isinstance(passages, list):
        return []

    reusable = []
    for passage in passages:
        if (
            not isinstance(passage, dict)
            or not isinstance(passage.get("text"), str)
            or not passage["text"].strip()
            or not isinstance(passage.get("file_id"), str)
            or not isinstance(passage.get("filename"), str)
            or (
                eligible_file_ids is not None
                and passage["file_id"] not in eligible_file_ids
            )
        ):
            continue
        reusable.append({**passage, "origin": "previous_turn"})
        if len(reusable) == MAX_REUSABLE_PASSAGES:
            break
    return reusable


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

    response = OpenAI(timeout=45, max_retries=0).vector_stores.search(
        vector_store_id=vector_store_id,
        query=query,
        max_num_results=limit,
        rewrite_query=False,
    )

    return deduplicate_passages([
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
    ])
