"""Validate the frozen evaluation fixtures and their deterministic policy labels."""

import json
from collections import Counter
from pathlib import Path

from rag.routing import MAX_ROUTED_REQUESTS, get_enabled_restriction


EXPECTED_GROUP_COUNTS = {
    "mn_cyrillic": 6,
    "mn_latin": 4,
    "english": 3,
    "mixed_typo": 3,
    "multi_intent": 4,
    "context": 4,
    "restricted": 3,
    "misc": 3,
}


def main() -> int:
    cases = json.loads((Path(__file__).with_name("cases.json")).read_text(encoding="utf-8"))
    counts = Counter(case["group"] for case in cases)
    assert len(cases) == 30 and counts == EXPECTED_GROUP_COUNTS, counts
    assert len({case["id"] for case in cases}) == len(cases)
    assert sum(case["split"] == "holdout" for case in cases) == 6

    policy_checks = 0
    for case in cases:
        assert isinstance(case["history"], list)
        assert 1 <= len(case["expected"]) <= MAX_ROUTED_REQUESTS
        assert case["source_verified"] is False
        for request in case["expected"]:
            intent = request["intent"]
            relationship = request["relationship"]
            expected_action = request["action"]
            actual_action = (
                "clarification"
                if relationship == "clarification"
                else "restricted"
                if get_enabled_restriction(intent)
                else "greeting"
                if intent == "greeting"
                else "out_of_scope"
                if intent == "general"
                else "search"
            )
            assert actual_action == expected_action, (case["id"], expected_action, actual_action)
            policy_checks += 1

    allowed_requests = sum(
        request["action"] == "search"
        for case in cases
        for request in case["expected"]
    )
    print(f"Fixture validation: {len(cases)} cases, {len(cases) - 6} development, 6 holdout")
    print(f"Expected deterministic policy outcomes checked: {policy_checks}")
    print(
        "30-case live logical-call ceiling: "
        f"LLM<={len(cases) + allowed_requests * 3} "
        f"(router={len(cases)}, reviewer<={allowed_requests * 2}, "
        f"generation<={allowed_requests}, Vector Store search<={allowed_requests * 2}"
        ")"
    )
    print("Live model-call bounds: max_completion_tokens=1200, timeout=45s, retries=0.")
    print("Offline run made no API calls; it cannot report live token usage or spend.")
    print("Source-grounded answer labels: 0 (not verified against indexed documents)")
    print("Model routing, normalization, retrieval, and answer quality were not measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
