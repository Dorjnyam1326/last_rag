import hashlib
import os
import re
from pathlib import Path


MAX_REUSABLE_PASSAGES = 5
_SEARCH_PLAN_STRATEGIES = {"single_query": 1, "multi_query": 2}
_REQUEST_ID = re.compile(r"R[1-9][0-9]*\Z")
_QUERY_ID = re.compile(r"Q[1-9][0-9]*\Z")
_FACT_ID = re.compile(r"F[1-9][0-9]*\Z")
_LOCATION_KEYS = (
    "chunk_id",
    "location_id",
    "page",
    "page_number",
    "section",
    "start_offset",
    "end_offset",
    "offset",
)
_VERSION_KEYS = ("version", "document_version", "effective_date", "effective_from", "effective_to")
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
    """Merge exact repeats from one file without losing retrieval associations."""
    unique = []
    identities = {}
    for passage in passages:
        text = passage.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        file_id = passage.get("file_id")
        identity = _passage_identity(passage)

        existing_index = identities.get(identity) if identity is not None else None
        if existing_index is None:
            current = dict(passage)
            if "query_id" in current:
                current["retrieval_matches"] = [_retrieval_match(current)]
            unique.append(current)
            if identity is not None:
                identities[identity] = len(unique) - 1
            continue

        existing = unique[existing_index]
        matches = existing.get("retrieval_matches")
        if not isinstance(matches, list):
            matches = _passage_matches(existing)
            existing["retrieval_matches"] = matches
        for match in _passage_matches(passage):
            if match not in matches:
                matches.append(match)
        current_targets = existing.get("targets")
        targets = list(current_targets) if isinstance(current_targets, list) else []
        for match in _passage_matches(passage):
            match_targets = match.get("targets")
            for target in match_targets if isinstance(match_targets, list) else []:
                if target not in targets:
                    targets.append(target)
        if targets:
            existing["targets"] = targets

        source = _source_provenance(passage)
        if source:
            sources = existing.get("source_provenance")
            if not isinstance(sources, list):
                sources = [_source_provenance(existing)]
                existing["source_provenance"] = sources
            if source not in sources:
                sources.append(source)
    return unique


def _source_provenance(passage: dict) -> dict:
    retrieval_keys = {
        "text", "passage_id", "query_id", "rank", "targets",
        "retrieval_matches", "source_provenance",
    }
    return {key: value for key, value in passage.items() if key not in retrieval_keys}


def _passage_identity(passage: dict) -> tuple | None:
    file_id = passage.get("file_id")
    text = passage.get("text")
    if not isinstance(file_id, str) or not file_id.strip():
        return None

    def scalar_metadata(keys: tuple[str, ...]) -> tuple:
        return tuple(
            (key, passage[key])
            for key in keys
            if isinstance(passage.get(key), (str, int, float))
            and not isinstance(passage.get(key), bool)
        )

    version = scalar_metadata(_VERSION_KEYS)
    location = scalar_metadata(_LOCATION_KEYS)
    fingerprint_text = " ".join(text.split())
    fingerprint = hashlib.sha256(fingerprint_text.encode("utf-8")).hexdigest()
    return file_id, version, location, fingerprint


def _retrieval_match(passage: dict) -> dict:
    return {
        key: value
        for key, value in (
            ("query_id", passage.get("query_id")),
            ("rank", passage.get("rank")),
            ("score", passage.get("score")),
            ("targets", passage.get("targets")),
        )
        if value is not None
    }


def _passage_matches(passage: dict) -> list[dict]:
    matches = passage.get("retrieval_matches")
    if isinstance(matches, list):
        return [item for item in matches if isinstance(item, dict)]
    if passage.get("query_id") is not None:
        return [_retrieval_match(passage)]
    return []


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


def _validate_search_plan(search_plan: dict) -> tuple[list[dict], int]:
    if not isinstance(search_plan, dict):
        raise ValueError("SearchPlan must be an object")

    request_id = search_plan.get("request_id")
    strategy_id = search_plan.get("strategy_id")
    expected_query_count = (
        _SEARCH_PLAN_STRATEGIES.get(strategy_id)
        if isinstance(strategy_id, str)
        else None
    )
    queries = search_plan.get("queries")
    limit = search_plan.get("limit_per_query")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("SearchPlan has an invalid request_id")
    if expected_query_count is None:
        raise ValueError("SearchPlan uses an unsupported strategy")
    if not isinstance(queries, list) or len(queries) != expected_query_count:
        raise ValueError("SearchPlan query count does not match its strategy")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit_per_query must be an integer between 1 and 50")

    seen_query_texts = set()
    for index, query in enumerate(queries, start=1):
        if not isinstance(query, dict):
            raise ValueError("SearchPlan query must be an object")
        query_id = query.get("id")
        text = query.get("text")
        targets = query.get("targets")
        normalized_text = " ".join(text.split()) if isinstance(text, str) else ""
        if (
            not isinstance(query_id, str)
            or not _QUERY_ID.fullmatch(query_id)
            or query_id != f"Q{index}"
            or not isinstance(text, str)
            or not text.strip()
            or normalized_text in seen_query_texts
            or not isinstance(targets, list)
            or not targets
            or any(
                not isinstance(target, str) or not _FACT_ID.fullmatch(target)
                for target in targets
            )
            or len(set(targets)) != len(targets)
        ):
            raise ValueError("SearchPlan contains an invalid query")
        seen_query_texts.add(normalized_text)

    return queries, limit


def execute_search_plan(search_plan: dict, *, search_provider=None) -> dict:
    """Execute a validated SearchPlan sequentially and retain query provenance."""
    queries, limit = _validate_search_plan(search_plan)
    search = search_documents if search_provider is None else search_provider
    if not callable(search):
        raise ValueError("search_provider must be callable")

    searches = []
    passages = []

    for query in queries:
        query_id = query["id"]
        targets = list(query["targets"])
        try:
            results = search(query["text"], limit=limit)
            if not isinstance(results, list) or any(
                not isinstance(result, dict) for result in results
            ):
                raise TypeError("search_documents must return a list of passage objects")
        except Exception as error:
            searches.append(
                {
                    "query_id": query_id,
                    "targets": targets,
                    "status": "error",
                    "result_count": 0,
                    "error_type": type(error).__name__,
                }
            )
            continue

        searches.append(
            {
                "query_id": query_id,
                "targets": targets,
                "status": "success" if results else "empty",
                "result_count": len(results),
            }
        )
        for rank, result in enumerate(results, start=1):
            passages.append(
                {
                    **result,
                    "passage_id": f"P{len(passages) + 1}",
                    "query_id": query_id,
                    "rank": rank,
                    "targets": list(targets),
                }
            )

    failed_searches = sum(search["status"] == "error" for search in searches)
    if failed_searches == len(searches):
        status = "error"
    elif failed_searches:
        status = "partial_error"
    elif passages:
        status = "success"
    else:
        status = "empty"

    return {
        "request_id": search_plan["request_id"],
        "status": status,
        "searches": searches,
        "passages": passages,
    }
