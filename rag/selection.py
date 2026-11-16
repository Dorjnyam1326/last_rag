import re
from collections.abc import Mapping

from rag.planning import CAPABILITY_IDS


_STRATEGIES = {
    "single_query": {
        "capabilities": frozenset({"focused_lookup"}),
        "search_calls": 1,
    },
    "multi_query": {
        "capabilities": frozenset({"focused_lookup", "multi_angle_coverage"}),
        "search_calls": 2,
    },
}
_REQUEST_ID = re.compile(r"R[1-3]\Z")
_FACT_ID = re.compile(r"F[1-9][0-9]*\Z")
_NEED_ID = re.compile(r"N[1-9][0-9]*\Z")
_RESULT_LIMIT = 5


def _validate_inputs(
    proposals: list[dict],
    resolved_questions: Mapping[str, str],
    remaining_search_budget: int,
    search_queries: Mapping[str, str] | None,
) -> list[tuple[dict, str, str]]:
    if not isinstance(proposals, list):
        raise ValueError("proposals must be a list")
    if not isinstance(resolved_questions, Mapping):
        raise ValueError("resolved_questions must be a request ID mapping")
    if search_queries is not None and not isinstance(search_queries, Mapping):
        raise ValueError("search_queries must be a request ID mapping")
    if (
        not isinstance(remaining_search_budget, int)
        or isinstance(remaining_search_budget, bool)
        or remaining_search_budget < 0
    ):
        raise ValueError("remaining_search_budget must be a non-negative integer")

    validated = []
    seen_requests = set()
    for proposal in proposals:
        if not isinstance(proposal, dict) or set(proposal) != {
            "request_id",
            "required_facts",
            "information_needs",
            "required_capabilities",
            "preferred_capabilities",
        }:
            raise ValueError("proposal does not match the PlanningProposal contract")

        request_id = proposal["request_id"]
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID.fullmatch(request_id)
            or request_id in seen_requests
        ):
            raise ValueError("proposal has an invalid or duplicate request ID")
        seen_requests.add(request_id)

        resolved_question = resolved_questions.get(request_id)
        if not isinstance(resolved_question, str) or not resolved_question.strip():
            raise ValueError("proposal needs a non-empty resolved question")
        query_text = (
            resolved_question
            if search_queries is None
            else search_queries.get(request_id)
        )
        if not isinstance(query_text, str) or not query_text.strip():
            raise ValueError("proposal needs a non-empty normalized search query")

        facts = proposal["required_facts"]
        needs = proposal["information_needs"]
        if not isinstance(facts, list) or not facts:
            raise ValueError("proposal must have required facts")
        if not isinstance(needs, list) or not needs:
            raise ValueError("proposal must have information needs")

        fact_ids = set()
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {"id", "description"}:
                raise ValueError("required fact does not match its contract")
            fact_id = fact["id"]
            description = fact["description"]
            if (
                not isinstance(fact_id, str)
                or not _FACT_ID.fullmatch(fact_id)
                or fact_id in fact_ids
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise ValueError("proposal has an invalid or duplicate fact")
            fact_ids.add(fact_id)

        need_ids = set()
        covered_fact_ids = set()
        for need in needs:
            if not isinstance(need, dict) or set(need) != {"id", "focus", "targets"}:
                raise ValueError("information need does not match its contract")
            need_id = need["id"]
            focus = need["focus"]
            targets = need["targets"]
            if (
                not isinstance(need_id, str)
                or not _NEED_ID.fullmatch(need_id)
                or need_id in need_ids
                or not isinstance(focus, str)
                or not focus.strip()
                or not isinstance(targets, list)
                or not targets
                or any(not isinstance(target, str) for target in targets)
                or len(set(targets)) != len(targets)
                or any(target not in fact_ids for target in targets)
            ):
                raise ValueError("proposal has an invalid need or fact target")
            need_ids.add(need_id)
            covered_fact_ids.update(targets)
        if covered_fact_ids != fact_ids:
            raise ValueError("information needs must target every required fact")

        required = proposal["required_capabilities"]
        preferred = proposal["preferred_capabilities"]
        for capabilities in (required, preferred):
            if (
                not isinstance(capabilities, list)
                or any(
                    not isinstance(capability, str) or capability not in CAPABILITY_IDS
                    for capability in capabilities
                )
                or len(set(capabilities)) != len(capabilities)
            ):
                raise ValueError("proposal has an invalid or duplicate capability")
        if not required or set(required) & set(preferred):
            raise ValueError("required capabilities must be non-empty and distinct")

        validated.append((proposal, resolved_question, query_text))

    request_ids = [proposal["request_id"] for proposal, _, _ in validated]
    if request_ids != sorted(request_ids, key=lambda value: int(value[1:])):
        raise ValueError("proposals must preserve Router request order")
    if set(resolved_questions) != set(request_ids):
        raise ValueError("resolved_questions must match the proposal request IDs")
    if search_queries is not None and set(search_queries) != set(request_ids):
        raise ValueError("search_queries must match the proposal request IDs")
    return validated


def _multi_query_reason(proposal: dict) -> str | None:
    needs = proposal["information_needs"]
    if len(needs) != 2:
        return "multi_query requires exactly two information needs"
    normalized_focuses = [" ".join(need["focus"].split()) for need in needs]
    if normalized_focuses[0] == normalized_focuses[1]:
        return "multi_query needs distinct information focuses"
    return None


def _query_texts(proposal: dict, query_text: str, strategy_id: str) -> list[dict]:
    if strategy_id == "single_query":
        return [
            {
                "id": "Q1",
                "text": query_text,
                "targets": [fact["id"] for fact in proposal["required_facts"]],
            }
        ]

    return [
        {
            "id": f"Q{index}",
            "text": f"{query_text} {need['focus']}",
            "targets": need["targets"],
        }
        for index, need in enumerate(proposal["information_needs"], start=1)
    ]


def _validate_search_plan(plan: dict, proposal: dict, allocated_budget: int) -> None:
    if set(plan) != {
        "request_id",
        "strategy_id",
        "queries",
        "limit_per_query",
        "fallback_applied",
        "fallback_reason",
    } or plan["request_id"] != proposal["request_id"]:
        raise ValueError("SearchPlan does not match its request contract")
    if plan["strategy_id"] not in _STRATEGIES:
        raise ValueError("SearchPlan uses an unknown strategy")
    strategy = _STRATEGIES[plan["strategy_id"]]
    queries = plan["queries"]
    fact_ids = {fact["id"] for fact in proposal["required_facts"]}
    query_ids = set()
    query_texts = set()
    covered_fact_ids = set()
    if (
        not isinstance(queries, list)
        or len(queries) != strategy["search_calls"]
        or len(queries) > allocated_budget
        or plan["limit_per_query"] != _RESULT_LIMIT
    ):
        raise ValueError("SearchPlan exceeds its strategy or allocated budget")

    for index, query in enumerate(queries, start=1):
        if not isinstance(query, dict) or set(query) != {"id", "text", "targets"}:
            raise ValueError("SearchPlan query does not match its contract")
        query_id = query["id"]
        text = query["text"]
        targets = query["targets"]
        normalized_text = " ".join(text.split()) if isinstance(text, str) else ""
        if (
            query_id != f"Q{index}"
            or query_id in query_ids
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(targets, list)
            or not targets
            or any(not isinstance(target, str) for target in targets)
            or any(target not in fact_ids for target in targets)
            or len(set(targets)) != len(targets)
            or normalized_text in query_texts
        ):
            raise ValueError("SearchPlan has an invalid or duplicate query")
        query_ids.add(query_id)
        query_texts.add(normalized_text)
        covered_fact_ids.update(targets)

    if covered_fact_ids != fact_ids:
        raise ValueError("SearchPlan queries must target every required fact")
    if (
        not isinstance(plan["fallback_applied"], bool)
        or (
            plan["fallback_reason"] is not None
            and (
                not isinstance(plan["fallback_reason"], str)
                or not plan["fallback_reason"].strip()
            )
        )
        or plan["fallback_applied"] != (plan["fallback_reason"] is not None)
    ):
        raise ValueError("SearchPlan fallback details are inconsistent")


def _limitation(request_id: str, reason: str) -> dict:
    return {"request_id": request_id, "status": "planning_limitation", "reason": reason}


def select_search_plans(
    proposals: list[dict],
    resolved_questions: Mapping[str, str],
    *,
    remaining_search_budget: int,
    search_queries: Mapping[str, str] | None = None,
) -> list[dict]:
    """Select and validate deterministic SearchPlans without executing retrieval."""
    inputs = _validate_inputs(
        proposals, resolved_questions, remaining_search_budget, search_queries
    )
    budget = remaining_search_budget
    outcomes = {}
    planned = []

    # Allocate the least-cost strategy satisfying required capabilities first.
    for proposal, resolved_question, query_text in inputs:
        request_id = proposal["request_id"]
        required = set(proposal["required_capabilities"])
        candidates = [
            strategy_id
            for strategy_id, strategy in _STRATEGIES.items()
            if required.issubset(strategy["capabilities"])
            and (strategy_id != "multi_query" or _multi_query_reason(proposal) is None)
        ]
        if not candidates:
            supported = set().union(
                *(strategy["capabilities"] for strategy in _STRATEGIES.values())
            )
            unsupported = sorted(required - supported)
            reason = (
                f"unsupported required capabilities: {', '.join(unsupported)}"
                if unsupported
                else _multi_query_reason(proposal)
                or "no V1 strategy satisfies the required capabilities"
            )
            outcomes[request_id] = _limitation(request_id, reason)
            continue

        strategy_id = min(
            candidates,
            key=lambda value: (_STRATEGIES[value]["search_calls"], value),
        )
        search_calls = _STRATEGIES[strategy_id]["search_calls"]
        if search_calls > budget:
            outcomes[request_id] = _limitation(
                request_id,
                f"requires {search_calls} search operations; only {budget} remain",
            )
            continue

        budget -= search_calls
        planned.append(
            {
                "request_id": request_id,
                "proposal": proposal,
                "resolved_question": resolved_question,
                "query_text": query_text,
                "strategy_id": strategy_id,
                "search_calls": search_calls,
                "preference_reason": None,
            }
        )

    # Use leftover operations for preferred multi-angle coverage in request order.
    for item in planned:
        proposal = item["proposal"]
        if (
            item["strategy_id"] == "single_query"
            and "multi_angle_coverage" in proposal["preferred_capabilities"]
        ):
            reason = _multi_query_reason(proposal)
            if reason:
                item["preference_reason"] = reason
            elif budget:
                item["strategy_id"] = "multi_query"
                item["search_calls"] = 2
                budget -= 1
            else:
                item["preference_reason"] = "insufficient budget for preferred multi_query"

    for item in planned:
        proposal = item["proposal"]
        strategy_id = item["strategy_id"]
        selected_capabilities = _STRATEGIES[strategy_id]["capabilities"]
        unmet_preferred = sorted(
            set(proposal["preferred_capabilities"]) - selected_capabilities
        )
        reasons = []
        if item["preference_reason"]:
            reasons.append(item["preference_reason"])
        unreported_preferred = [
            capability
            for capability in unmet_preferred
            if not (
                item["preference_reason"]
                and capability == "multi_angle_coverage"
            )
        ]
        if unreported_preferred:
            reasons.append(
                "preferred capabilities not provided: "
                + ", ".join(unreported_preferred)
            )

        plan = {
            "request_id": item["request_id"],
            "strategy_id": strategy_id,
            "queries": _query_texts(
                proposal, item["query_text"], strategy_id
            ),
            "limit_per_query": _RESULT_LIMIT,
            "fallback_applied": bool(reasons),
            "fallback_reason": "; ".join(reasons) or None,
        }
        _validate_search_plan(plan, proposal, item["search_calls"])
        outcomes[item["request_id"]] = {
            "request_id": item["request_id"],
            "status": "planned",
            "search_plan": plan,
        }

    if sum(item["search_calls"] for item in planned) > remaining_search_budget:
        raise ValueError("selected SearchPlans exceed the caller-provided budget")
    return [outcomes[proposal["request_id"]] for proposal, _, _ in inputs]
