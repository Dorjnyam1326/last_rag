"""Opt-in, resumable live evaluation. Detailed output remains git-ignored."""

import argparse
import csv
import json
import os
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.callbacks import get_usage_metadata_callback


ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "evaluation" / "cases.json"
TRACE_FILE = ROOT / "evaluation" / "live_results.json"
REVIEW_FILE = ROOT / "evaluation" / "human_review.csv"
SMOKE_IDS = ("C01", "C08", "C14", "C17", "C18", "C21", "C25", "C30")
MAX_OUTPUT_TOKENS = 1200
MAX_MODEL_CALLS_PER_CASE = 10  # 1 router + up to 3 requests * (2 reviews + 1 answer)
MODEL_CONTEXT_TOKENS = 1_050_000
STOP_USD = Decimal("15")
REGIONAL_UPLIFT = Decimal("1.10")


def _worst_call_cost() -> Decimal:
    # Conservative high-context Standard rates, plus the possible regional uplift.
    input_cost = Decimal(MODEL_CONTEXT_TOKENS) * Decimal("0.20") / 1_000_000
    output_cost = Decimal(MAX_OUTPUT_TOKENS) * Decimal("0.75") / 1_000_000
    return (input_cost + output_cost) * REGIONAL_UPLIFT


MAX_CASE_COST = _worst_call_cost() * MAX_MODEL_CALLS_PER_CASE
CALL_KEYS = ("router", "reviewer", "generation", "vector_store_search")


def _add_call_counts(total: dict, addition: dict) -> dict:
    return {key: total.get(key, 0) + addition.get(key, 0) for key in CALL_KEYS}


def _usage_cost(usage: dict) -> tuple[dict, Decimal]:
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    long_context = input_tokens > 272_000
    input_rate = Decimal("0.20" if long_context else "0.10")
    output_rate = Decimal("0.75" if long_context else "0.50")
    estimate = (
        Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
    ) / 1_000_000 * REGIONAL_UPLIFT
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens", input_tokens + output_tokens)),
    }, estimate


def _text_evidence(passages: list[dict]) -> bool:
    return any(isinstance(p.get("text"), str) and p["text"].strip() for p in passages)


def _safe_request(request: dict) -> dict:
    return {
        key: request.get(key)
        for key in ("intent", "relationship", "search_query", "resolved_question")
    }


def _safe_result(result: dict) -> dict:
    fields = ("answer", "citations", "status", "request_id", "question")
    clean = {key: result[key] for key in fields if key in result}
    if result.get("results"):
        clean["results"] = [
            {key: item[key] for key in fields if key in item}
            for item in result["results"]
        ]
    return clean


def _check_case(case: dict, route: dict, result: dict, trace: dict) -> dict:
    expected = case["expected"]
    routed = route.get("requests", []) if route else []
    grouped = result.get("results") or [result]
    route_count_ok = len(routed) == len(expected)
    route_items_ok = route_count_ok and all(
        item.get("relationship") == want["relationship"]
        and (
            want["relationship"] == "clarification"
            or item.get("intent") == want["intent"]
        )
        for item, want in zip(routed, expected)
    )
    action_checks = []
    for index, want in enumerate(expected, start=1):
        rid = f"R{index}"
        item = grouped[index - 1] if index <= len(grouped) else {}
        searches = sum(c.get("request_id") == rid for c in trace["searches"])
        generations = sum(
            c.get("request_id") == rid and c.get("llm_attempted")
            for c in trace["generations"]
        )
        action = want["action"]
        if action == "search":
            ok = searches > 0 and item.get("status") not in {
                "restricted", "clarification", "greeting", "out_of_scope"
            }
        elif action == "restricted":
            ok = item.get("status") == "restricted" and searches == 0 and generations == 0
        elif action == "clarification":
            ok = item.get("status") == "clarification" and searches == 0 and generations == 0
        elif action == "greeting":
            ok = item.get("status") == "greeting" and searches == 0 and generations == 0
        else:  # out_of_scope
            ok = item.get("status") == "out_of_scope" and searches == 0 and generations == 0
        action_checks.append({"request_id": rid, "expected": action, "passed": ok})
    return {
        "request_count_match": route_count_ok,
        "intent_relationship_match": route_items_ok,
        "actions": action_checks,
        "passed": route_count_ok and route_items_ok and all(c["passed"] for c in action_checks),
    }


def _write_review_sheet(cases: list[dict], results: dict) -> None:
    with REVIEW_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "case_id", "split", "question", "status", "answer", "citations",
                "policy_correct", "evidence_supports_claims", "citations_support_claims",
                "unsupported_claims", "reviewer_notes",
            ],
        )
        writer.writeheader()
        for case in cases:
            item = results.get(case["id"])
            if item:
                writer.writerow({
                    "case_id": case["id"],
                    "split": case["split"],
                    "question": case["question"],
                    "status": item["result"].get("status"),
                    "answer": item["result"].get("answer"),
                    "citations": json.dumps(item["result"].get("citations", []), ensure_ascii=False),
                    "policy_correct": "",
                    "evidence_supports_claims": "",
                    "citations_support_claims": "",
                    "unsupported_claims": "",
                    "reviewer_notes": "",
                })


def _save(state: dict, cases: list[dict]) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat()
    temp = TRACE_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(TRACE_FILE)
    _write_review_sheet(cases, state["cases"])


def _refresh_metrics(state: dict, by_id: dict) -> None:
    for case_id, trace in state["cases"].items():
        if case_id in by_id:
            trace["objective_checks"] = _check_case(
                by_id[case_id], {"requests": trace.get("route") or []}, trace["result"], trace
            )
    expected = {case_id: by_id[case_id] for case_id in state["cases"] if case_id in by_id}
    intent_total = intent_matches = relationship_matches = request_total = 0
    for case_id, case in expected.items():
        routed = state["cases"][case_id].get("route") or []
        for want, actual in zip(case["expected"], routed):
            request_total += 1
            if want["relationship"] != "clarification":
                intent_total += 1
                intent_matches += actual.get("intent") == want["intent"]
            relationship_matches += actual.get("relationship") == want["relationship"]
    action_checks = [
        action
        for trace in state["cases"].values()
        for action in trace["objective_checks"]["actions"]
    ]
    review_statuses = [
        review.get("status") for trace in state["cases"].values() for review in trace["reviews"]
    ]
    result_statuses = [
        result.get("status")
        for trace in state["cases"].values()
        for result in (trace["result"].get("results") or [trace["result"]])
    ]
    retries = sum(
        search.get("attempt") == "retry"
        for trace in state.get("runs", state["cases"].values())
        for search in trace["searches"]
    )
    usage_records = []
    for run in state.get("runs", state["cases"].values()):
        router_usage = run.get("router_call", {}).get("usage") or {}
        if router_usage.get("usage_available"):
            usage_records.append(run["router_call"]["usage"])
        for group in ("reviews", "generations"):
            usage_records.extend(
                item["usage"] for item in run.get(group, [])
                if (item.get("usage") or {}).get("usage_available")
            )
    model_call_count = sum(state.get("calls", {}).get(key, 0) for key in ("router", "reviewer", "generation"))
    errors = sum(
        sum(result.get("status") == "error" for result in (run["result"].get("results") or [run["result"]]))
        + sum("error_type" in item for group in ("searches", "reviews", "generations") for item in run.get(group, []))
        for run in state.get("runs", state["cases"].values())
    )
    state["metrics"] = {
        "completed_cases": len(state["cases"]),
        "case_attempts": len(state.get("runs", [])) + len(state.get("accounting_adjustments", [])),
        "holdout_completed": sum(trace["split"] == "holdout" for trace in state["cases"].values()),
        "request_count_matches": sum(trace["objective_checks"]["request_count_match"] for trace in state["cases"].values()),
        "intent_accuracy": f"{intent_matches}/{intent_total}",
        "relationship_matches": f"{relationship_matches}/{request_total}",
        "expected_action_matches": f"{sum(a['passed'] for a in action_checks)}/{len(action_checks)}",
        "multi_intent_cases": sum(len(case["expected"]) > 1 for case in expected.values()),
        "multi_intent_cases_with_correct_request_count": sum(
            len(case["expected"]) > 1 and state["cases"][case_id]["objective_checks"]["request_count_match"]
            for case_id, case in expected.items()
        ),
        "follow_up_cases": sum(
            request.get("relationship") == "follow_up"
            for case in expected.values() for request in case["expected"]
        ),
        "follow_up_relationship_matches": sum(
            actual.get("relationship") == "follow_up"
            for trace in state["cases"].values() for actual in (trace.get("route") or [])
        ),
        "review_status_counts": {status: review_statuses.count(status) for status in sorted(set(review_statuses))},
        "final_status_counts": {status: result_statuses.count(status) for status in sorted(set(result_statuses))},
        "retry_search_count": retries,
        "source_verified_cases": sum(bool(case["source_verified"]) for case in expected.values()),
        "api_usage": {
            "usage_records_available": len(usage_records),
            "model_calls": model_call_count,
            "usage_records_missing": model_call_count - len(usage_records),
            "input_tokens_recorded": sum(item.get("input_tokens", 0) for item in usage_records),
            "output_tokens_recorded": sum(item.get("output_tokens", 0) for item in usage_records),
            "total_tokens_recorded": sum(item.get("total_tokens", 0) for item in usage_records),
            "complete": model_call_count == len(usage_records),
        },
        "error_count": errors,
        "human_answer_quality": "pending human review; no verified answer labels",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true", help="Required to make paid API calls")
    parser.add_argument("--all-cases", action="store_true", help="Run all 30 cases, including holdout")
    parser.add_argument("--case-ids", nargs="+", help="Run selected cases; default is the eight-case smoke set")
    parser.add_argument("--rerun-cases", nargs="+", help="Rerun only these selected cases")
    args = parser.parse_args()
    if args.all_cases and args.case_ids:
        parser.error("Use either --all-cases or --case-ids, not both")
    if not args.confirm_live:
        print("No API calls made. Add --confirm-live to opt in.")
        return 2

    load_dotenv(ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", "gpt-6-luna")
    if (
        model != "gpt-6-luna"
        or not os.getenv("OPENAI_API_KEY")
        or not os.getenv("OPENAI_VECTOR_STORE_ID")
        or os.getenv("OPENAI_BASE_URL")
    ):
        print("Live run stopped: configured model/provider or required credentials do not match the verified pricing scope.")
        return 2

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}
    if args.all_cases:
        selected = cases
    else:
        ids = args.case_ids or list(SMOKE_IDS)
        if any(case_id not in by_id for case_id in ids):
            parser.error("Unknown evaluation case ID")
        selected = [by_id[case_id] for case_id in ids]
    rerun_ids = set(args.rerun_cases or [])
    if not rerun_ids.issubset({case["id"] for case in selected}):
        parser.error("--rerun-cases must be included in --case-ids")
    if args.all_cases:
        selected = sorted(selected, key=lambda case: (case["split"] == "holdout", case["id"]))

    if TRACE_FILE.exists():
        state = json.loads(TRACE_FILE.read_text(encoding="utf-8"))
        if state.get("model") != model or state.get("service_tier") != "default":
            print("Live run stopped: existing trace uses a different model or processing tier.")
            return 2
        state.setdefault("runs", list(state.get("cases", {}).values()))
        state.setdefault("accounting_adjustments", [])
    else:
        state = {
            "model": model,
            "service_tier": "default",
            "rates_usd_per_million": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
            "cost_estimate_includes_regional_uplift_percent": 10,
            "max_completion_tokens_per_model_call": MAX_OUTPUT_TOKENS,
            "request_timeout_seconds": 45,
            "max_retries": 0,
            "calls": {"router": 0, "reviewer": 0, "generation": 0, "vector_store_search": 0},
            "estimated_cost_upper_bound_usd": "0",
            "cases": {},
        }

    remaining_cost = Decimal(state["estimated_cost_upper_bound_usd"])
    pending = [
        case for case in selected
        if case["id"] not in state["cases"] or case["id"] in rerun_ids
    ]
    if not pending:
        _refresh_metrics(state, by_id)
        _save(state, cases)
        print("All selected cases already have results; no API calls made.")
        return 0

    from rag import pipeline
    from rag.pipeline import answer_question

    total = len(pending)
    for case_index, case in enumerate(pending, start=1):
        if remaining_cost + MAX_CASE_COST > STOP_USD:
            print("Stopped before next case: conservative estimate would cross $15.")
            break

        trace = {
            "case_id": case["id"], "split": case["split"], "question": case["question"],
            "history": case["history"], "route": None, "searches": [], "reviews": [],
            "generations": [], "calls": {"router": 0, "reviewer": 0, "generation": 0, "vector_store_search": 0},
            "estimated_cost_upper_bound_usd": "0",
        }
        request_index = 0
        active_trace = trace
        active_request: ContextVar[str] = ContextVar("evaluation_request_id", default="R0")
        cost_for_case = Decimal("0")

        def record_usage(component: str, callback, attempted: bool) -> tuple[dict | None, Decimal]:
            nonlocal cost_for_case, remaining_cost
            usage_by_model = callback.usage_metadata
            if usage_by_model:
                usage = next(iter(usage_by_model.values()))
                token_record, cost = _usage_cost(usage)
                entry = {"model": next(iter(usage_by_model)), **token_record, "usage_available": True}
            elif attempted:
                cost = _worst_call_cost()
                entry = {"model": model, "usage_available": False, "estimated_at_max_context": True}
            else:
                return None, Decimal("0")
            cost_for_case += cost
            remaining_cost += cost
            trace["calls"][component] += 1
            return entry, cost

        original_router = pipeline.classify_intent
        original_search = pipeline.search_documents
        original_review = pipeline.review_evidence
        original_generation = pipeline.generate_answer
        original_process = pipeline._process_request

        def tracked_router(question, history=None):
            started = time.perf_counter()
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_router(question, history)
                    trace["route"] = [_safe_request(r) for r in value.get("requests", [])]
                    return value
                finally:
                    usage, cost = record_usage("router", callback, attempted=True)
                    trace.setdefault("router_call", {}).update({
                        "latency_seconds": round(time.perf_counter() - started, 3),
                        "usage": usage, "estimated_cost_usd": str(cost),
                    })

        def tracked_search(query, limit=5):
            started = time.perf_counter()
            request_id = active_request.get()
            ordinal = 1 + sum(item["request_id"] == request_id for item in trace["searches"])
            record = {
                "request_id": request_id,
                "attempt": "initial" if ordinal == 1 else "retry",
                "query": query,
                "limit": limit,
            }
            trace["calls"]["vector_store_search"] += 1
            try:
                results = original_search(query, limit)
                record["results"] = [
                    {"file_id": item.get("file_id"), "score": item.get("score")}
                    for item in results
                ]
                return results
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["latency_seconds"] = round(time.perf_counter() - started, 3)
                trace["searches"].append(record)

        def tracked_review(question, passages):
            started = time.perf_counter()
            attempted = _text_evidence(passages)
            record = {"request_id": active_request.get(), "question": question}
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_review(question, passages)
                    record.update({
                        "status": value.get("status"),
                        "relevant_ids": value.get("relevant_ids", []),
                        "missing_information": value.get("missing_information", ""),
                        "retry_query": value.get("retry_query", ""),
                    })
                    return value
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    raise
                finally:
                    usage, cost = record_usage("reviewer", callback, attempted)
                    record.update({
                        "usage": usage,
                        "estimated_cost_usd": str(cost),
                        "latency_seconds": round(time.perf_counter() - started, 3),
                    })
                    trace["reviews"].append(record)

        def tracked_generation(question, passages, *args, **kwargs):
            started = time.perf_counter()
            attempted = _text_evidence(passages)
            record = {
                "request_id": active_request.get(), "question": question,
                "llm_attempted": attempted,
                "sources": [{"file_id": p.get("file_id"), "filename": p.get("filename")} for p in passages],
            }
            with get_usage_metadata_callback() as callback:
                try:
                    value = original_generation(question, passages, *args, **kwargs)
                    record.update({
                        "status": value.get("status"), "citations": value.get("citations", []),
                    })
                    return value
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    raise
                finally:
                    usage, cost = record_usage("generation", callback, attempted)
                    record.update({
                        "usage": usage,
                        "estimated_cost_usd": str(cost),
                        "latency_seconds": round(time.perf_counter() - started, 3),
                    })
                    trace["generations"].append(record)

        def tracked_process(request, catch_errors):
            nonlocal request_index
            request_index += 1
            token = active_request.set(f"R{request_index}")
            try:
                return original_process(request, catch_errors)
            finally:
                active_request.reset(token)

        pipeline.classify_intent = tracked_router
        pipeline.search_documents = tracked_search
        pipeline.review_evidence = tracked_review
        pipeline.generate_answer = tracked_generation
        pipeline._process_request = tracked_process
        started = time.perf_counter()
        uncaught_error = None
        try:
            result = answer_question(case["question"], history=case["history"] or None)
        except Exception as exc:
            uncaught_error = type(exc).__name__
            result = {
                "answer": "Evaluation request failed; see the recorded error type.",
                "citations": [], "status": "error", "passages": [],
            }
        finally:
            pipeline.classify_intent = original_router
            pipeline.search_documents = original_search
            pipeline.review_evidence = original_review
            pipeline.generate_answer = original_generation
            pipeline._process_request = original_process
        trace.update({
            "result": _safe_result(result),
            "uncaught_error_type": uncaught_error,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "estimated_cost_upper_bound_usd": str(cost_for_case),
            "objective_checks": _check_case(case, {"requests": trace["route"] or []}, result, trace),
        })
        state["cases"][case["id"]] = trace
        state.setdefault("runs", []).append(trace)
        state["estimated_cost_upper_bound_usd"] = str(remaining_cost)
        state["calls"] = _add_call_counts(state.get("calls", {}), trace["calls"])
        _save(state, cases)
        print(f"{case['id']} {case['split']}: {result.get('status')} · cost upper-bound ${cost_for_case:.6f} · {case_index}/{total}")

    _refresh_metrics(state, by_id)
    _save(state, cases)
    print(f"Total completed={len(state['cases'])}; estimated spend upper-bound=${remaining_cost:.6f}; stop threshold=$15.")
    print(f"Local trace: {TRACE_FILE}; human review sheet: {REVIEW_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
