import unittest
from decimal import Decimal

from evaluation.run_live import (
    CALL_KEYS,
    MAX_CASE_COST,
    STOP_USD,
    _add_call_counts,
    _check_case,
    _safe_result,
    _usage_cost,
)


class LiveEvaluationAccountingTests(unittest.TestCase):
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
            {"router": 8, "reviewer": 14, "generation": 7, "vector_store_search": 14},
            {"router": 1, "reviewer": 2, "generation": 1, "vector_store_search": 2},
        )

        self.assertEqual(totals, dict(zip(CALL_KEYS, (9, 16, 8, 16))))

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
