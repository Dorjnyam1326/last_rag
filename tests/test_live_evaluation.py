import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation.run_live import (
    CALL_KEYS,
    MAX_CASE_COST,
    STOP_USD,
    _add_call_counts,
    _append_session_history,
    _check_case,
    _load_question_set,
    _refresh_metrics,
    _safe_error_type,
    _safe_result,
    _usage_cost,
)


class LiveEvaluationAccountingTests(unittest.TestCase):
    def test_external_question_set_keeps_labels_and_validates_session_order(self):
        rows = [
            {
                "id": 1, "language": "mn_cyrillic", "session_id": "s1",
                "turn_index": 1, "follow_up": False, "query": "Чөлөө яаж авах вэ?",
                "expected_intents": ["leave request procedure"],
            },
            {
                "id": 2, "language": "mn_cyrillic", "session_id": "s1",
                "turn_index": 2, "follow_up": True, "query": "Хэн батлах вэ?",
                "expected_intents": ["leave approver"],
            },
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            cases, digest = _load_question_set(path)

            self.assertEqual([case["id"] for case in cases], ["LQ01", "LQ02"])
            self.assertEqual(cases[1]["expected_intent_labels"], ["leave approver"])
            self.assertTrue(cases[1]["follow_up"])
            self.assertEqual(cases[0]["expected"][0]["relationship"], "new")
            self.assertEqual(cases[1]["expected"][0]["relationship"], "follow_up")
            self.assertEqual(len(digest), 64)

            rows[1]["turn_index"] = 3
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "turn order"):
                _load_question_set(path)

    def test_external_follow_up_history_uses_the_previous_live_answer(self):
        history_by_session = {}
        first = {
            "session_id": "s1",
            "question": "Ээлжийн амралтаа яаж хүсэх вэ?",
        }
        _append_session_history(history_by_session, first, "Skyteams-д хүсэлт оруулна.")

        self.assertEqual(history_by_session["s1"], [
            {"role": "user", "content": first["question"]},
            {"role": "assistant", "content": "Skyteams-д хүсэлт оруулна."},
        ])

    def test_external_question_labels_do_not_claim_semantic_accuracy(self):
        case = {
            "expected": [{"intent": None, "relationship": "new", "action": "search"}],
        }
        route = {"requests": [{"intent": "workplace", "relationship": "new"}]}
        result = {"status": "answered"}
        trace = {"searches": [{"request_id": "R1"}], "generations": []}

        check = _check_case(case, route, result, trace)

        self.assertTrue(check["request_count_match"])
        self.assertTrue(check["intent_relationship_match"])
        self.assertTrue(check["actions"][0]["passed"])
        self.assertTrue(check["passed"])

        state = {
            "cases": {"LQ01": {
                "route": route["requests"],
                "searches": trace["searches"],
                "reviews": [],
                "generations": [],
                "calls": {key: 0 for key in CALL_KEYS},
                "result": result,
                "split": "external_unlabeled",
            }},
            "runs": [],
            "calls": {key: 0 for key in CALL_KEYS},
        }
        _refresh_metrics(state, {"LQ01": {**case, "source_verified": False}})
        self.assertEqual(state["metrics"]["intent_accuracy"], "0/0")
        self.assertEqual(state["metrics"]["relationship_matches"], "1/1")

    def test_refresh_metrics_counts_review_status_and_tolerates_unrecorded_legacy_status(self):
        trace = {
            "case_id": "C21",
            "split": "development",
            "route": [{"intent": "workplace", "relationship": "follow_up"}],
            "searches": [],
            "reviews": [{"review_status": "complete"}, {"status": None}],
            "generations": [],
            "calls": {key: 0 for key in CALL_KEYS},
            "result": {"status": "partially_answered", "citations": [], "answer": "safe"},
        }
        state = {"cases": {"C21": trace}, "runs": [trace], "calls": {key: 0 for key in CALL_KEYS}}
        by_id = {
            "C21": {
                "expected": [{
                    "intent": "workplace",
                    "relationship": "follow_up",
                    "action": "retrieval",
                }],
                "source_verified": False,
            }
        }

        _refresh_metrics(state, by_id)

        self.assertEqual(
            state["metrics"]["review_status_counts"],
            {"complete": 1, "unrecorded": 1},
        )

    def test_estimates_standard_token_usage_with_regional_buffer(self):
        tokens, cost = _usage_cost({"input_tokens": 1000, "output_tokens": 500})

        self.assertEqual(tokens["total_tokens"], 1500)
        self.assertEqual(cost, Decimal("0.000385"))

    def test_long_context_rate_is_applied(self):
        _, cost = _usage_cost({"input_tokens": 300_000, "output_tokens": 100})

        self.assertEqual(cost, Decimal("0.0660825"))

    def test_worst_case_question_reserve_stays_within_internal_threshold(self):
        self.assertLess(MAX_CASE_COST, STOP_USD)

    def test_saved_result_omits_retrieved_passage_text(self):
        result = _safe_result({
            "answer": "answer",
            "citations": [],
            "status": "answered",
            "passages": [{"text": "private evidence"}],
        })

        self.assertEqual(result, {"answer": "answer", "citations": [], "status": "answered"})

    def test_call_ledger_adds_reruns_instead_of_replacing_previous_counts(self):
        totals = _add_call_counts(
            {"router": 8, "planner": 5, "reviewer": 14, "generation": 7, "vector_store_search": 14},
            {"router": 1, "planner": 2, "reviewer": 2, "generation": 1, "vector_store_search": 2},
        )

        self.assertEqual(totals, dict(zip(CALL_KEYS, (9, 7, 16, 8, 16))))

    def test_safe_result_keeps_stage_status_without_passage_text(self):
        result = _safe_result({
            "answer": "answer",
            "citations": [],
            "status": "error",
            "retrieval_status": "partial_error",
            "planning_status": "planning_limitation",
            "retry_error_type": "TimeoutError",
            "passages": [{"text": "private evidence"}],
        })

        self.assertEqual(result, {
            "answer": "answer",
            "citations": [],
            "status": "error",
            "retrieval_status": "partial_error",
            "planning_status": "planning_limitation",
            "retry_error_type": "TimeoutError",
        })

    def test_safe_error_type_keeps_only_exception_class_names(self):
        self.assertEqual(_safe_error_type({"error_type": "RateLimitError"}), "RateLimitError")
        self.assertIsNone(_safe_error_type({"error_type": "OPENAI_API_KEY=secret"}))
        self.assertIsNone(_safe_error_type({"error_type": None}))

    def test_greeting_and_out_of_scope_actions_are_checked_correctly(self):
        trace = {"searches": [], "generations": []}
        for intent, action, status in (
            ("greeting", "greeting", "greeting"),
            ("general", "out_of_scope", "out_of_scope"),
        ):
            case = {"expected": [{"intent": intent, "relationship": "new", "action": action}]}
            route = {"requests": [{"intent": intent, "relationship": "new"}]}
            result = {"status": status}

            self.assertTrue(_check_case(case, route, result, trace)["passed"])

    def test_clarification_does_not_require_a_semantic_intent_label(self):
        case = {"expected": [{"intent": "workplace", "relationship": "clarification", "action": "clarification"}]}
        route = {"requests": [{"intent": "general", "relationship": "clarification"}]}

        self.assertTrue(_check_case(case, route, {"status": "clarification"}, {"searches": [], "generations": []})["passed"])


if __name__ == "__main__":
    unittest.main()
