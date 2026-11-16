import unittest

from rag import selection
from rag.selection import select_search_plans


def proposal(
    request_id="R1",
    *,
    facts=("F1",),
    focuses=("Журмын шаардлага",),
    targets=None,
    required=("focused_lookup",),
    preferred=(),
):
    if targets is None:
        targets = [(fact_id,) for fact_id in facts]
    return {
        "request_id": request_id,
        "required_facts": [
            {"id": fact_id, "description": f"Батлах шаардлагатай {fact_id}"}
            for fact_id in facts
        ],
        "information_needs": [
            {"id": f"N{index}", "focus": focus, "targets": list(targets[index - 1])}
            for index, focus in enumerate(focuses, start=1)
        ],
        "required_capabilities": list(required),
        "preferred_capabilities": list(preferred),
    }


def select(proposals, budget, questions=None, search_queries=None):
    if questions is None:
        questions = {
            item["request_id"]: f"Асуултын эх хэлбэр {item['request_id']}?"
            for item in proposals
        }
    return select_search_plans(
        proposals,
        questions,
        remaining_search_budget=budget,
        search_queries=search_queries,
    )


class SelectSearchPlansTests(unittest.TestCase):
    def test_selects_single_query_and_targets_all_required_facts(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Зөвшөөрөх эрх", "Хүсэлт бүртгэх"),
        )
        question = "3 цагийн чөлөөг хэн зөвшөөрөх, хаана хүсэлт гаргах вэ?"

        [result] = select([item], 1, {"R1": question})

        self.assertEqual(result["status"], "planned")
        plan = result["search_plan"]
        self.assertEqual(plan["strategy_id"], "single_query")
        self.assertEqual(plan["queries"], [{
            "id": "Q1",
            "text": question,
            "targets": ["F1", "F2"],
        }])
        self.assertFalse(plan["fallback_applied"])

    def test_uses_router_normalized_query_when_supplied(self):
        item = proposal()
        resolved_question = "bi margaash chuluu yaj awah ve?"
        normalized_query = "Маргааш чөлөө хэрхэн авах вэ?"

        [result] = select(
            [item],
            1,
            {"R1": resolved_question},
            {"R1": normalized_query},
        )

        self.assertEqual(
            result["search_plan"]["queries"][0]["text"], normalized_query
        )

    def test_rejects_search_query_mapping_with_missing_or_blank_values(self):
        item = proposal()
        question = {"R1": "Асуултын бүрэн хэлбэр"}

        for search_queries in ({}, {"R1": "  "}, {"R2": "Хамааралгүй"}):
            with self.subTest(search_queries=search_queries):
                with self.assertRaises(ValueError):
                    select([item], 1, question, search_queries)

    def test_selects_multi_query_and_preserves_question_in_both_queries(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Менежер эзгүй үеийн зөвшөөрөх эрх", "Хүсэлт бүртгэх журам"),
            preferred=("multi_angle_coverage",),
        )
        question = "3 цагийн чөлөө авахад менежер байхгүй бол хэн зөвшөөрөх вэ?"

        [result] = select([item], 2, {"R1": question})

        plan = result["search_plan"]
        self.assertEqual(plan["strategy_id"], "multi_query")
        self.assertEqual([query["id"] for query in plan["queries"]], ["Q1", "Q2"])
        self.assertTrue(all(query["text"].startswith(question) for query in plan["queries"]))
        self.assertEqual([query["targets"] for query in plan["queries"]], [["F1"], ["F2"]])
        self.assertEqual(plan["limit_per_query"], 5)
        self.assertFalse(plan["fallback_applied"])

    def test_required_multi_angle_cannot_fall_back_when_budget_is_short(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Нэг өнцөг", "Өөр өнцөг"),
            required=("focused_lookup", "multi_angle_coverage"),
        )

        [result] = select([item], 1)

        self.assertEqual(result["status"], "planning_limitation")
        self.assertIn("requires 2 search operations", result["reason"])

    def test_preferred_multi_angle_falls_back_to_single_when_budget_is_short(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Нэг өнцөг", "Өөр өнцөг"),
            preferred=("multi_angle_coverage",),
        )

        [result] = select([item], 1)

        plan = result["search_plan"]
        self.assertEqual(plan["strategy_id"], "single_query")
        self.assertTrue(plan["fallback_applied"])
        self.assertIn("insufficient budget", plan["fallback_reason"])

    def test_unsupported_required_capability_returns_limitation(self):
        item = proposal(required=("focused_lookup", "exact_reference_focus"))

        [result] = select([item], 5)

        self.assertEqual(result["status"], "planning_limitation")
        self.assertIn("exact_reference_focus", result["reason"])

    def test_unmet_unsupported_preference_is_recorded_but_does_not_block_plan(self):
        item = proposal(preferred=("comparison_coverage",))

        [result] = select([item], 1)

        self.assertEqual(result["status"], "planned")
        self.assertTrue(result["search_plan"]["fallback_applied"])
        self.assertIn("comparison_coverage", result["search_plan"]["fallback_reason"])

    def test_allocates_batch_budget_in_request_order_without_dropping_requests(self):
        proposals = [proposal(f"R{index}") for index in (1, 2, 3)]

        results = select(proposals, 2)

        self.assertEqual([result["request_id"] for result in results], ["R1", "R2", "R3"])
        self.assertEqual([result["status"] for result in results], [
            "planned", "planned", "planning_limitation"
        ])
        self.assertIn("only 0 remain", results[2]["reason"])
        self.assertEqual(
            sum(
                len(result["search_plan"]["queries"])
                for result in results
                if result["status"] == "planned"
            ),
            2,
        )

    def test_allocates_remaining_budget_to_preferred_multi_in_request_order(self):
        proposals = [
            proposal(
                "R1",
                facts=("F1", "F2"),
                focuses=("Зөвшөөрөх эрх", "Хүсэлт бүртгэх"),
                preferred=("multi_angle_coverage",),
            ),
            proposal("R2"),
        ]

        results = select(proposals, 3)

        self.assertEqual(
            [result["search_plan"]["strategy_id"] for result in results],
            ["multi_query", "single_query"],
        )

    def test_uses_the_supplied_budget_without_a_hardcoded_message_cap(self):
        proposals = [
            proposal(
                f"R{index}",
                facts=("F1", "F2"),
                focuses=(f"Need {index}A", f"Need {index}B"),
                preferred=("multi_angle_coverage",),
            )
            for index in (1, 2, 3)
        ]

        results = select(proposals, 6)

        self.assertEqual(
            [result["search_plan"]["strategy_id"] for result in results],
            ["multi_query", "multi_query", "multi_query"],
        )
        self.assertEqual(
            sum(len(result["search_plan"]["queries"]) for result in results),
            6,
        )

    def test_each_request_keeps_its_question_and_fact_targets_isolated(self):
        proposals = [
            proposal("R1", facts=("F1",), focuses=("Нууц үг шинэчлэх",)),
            proposal("R3", facts=("F1",), focuses=("Чөлөөний хүсэлт",)),
        ]
        questions = {"R1": "Нууц үгээ хаана шинэчлэх вэ?", "R3": "Чөлөөг хаана хүсэх вэ?"}

        results = select(proposals, 2, questions)

        self.assertEqual([result["request_id"] for result in results], ["R1", "R3"])
        self.assertEqual(
            [result["search_plan"]["queries"][0]["text"] for result in results],
            list(questions.values()),
        )
        self.assertTrue(all(
            result["search_plan"]["queries"][0]["targets"] == ["F1"]
            for result in results
        ))

    def test_duplicate_multi_query_focus_returns_limitation_when_multi_is_required(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Нэг өнцөг", "  Нэг   өнцөг  "),
            required=("focused_lookup", "multi_angle_coverage"),
        )

        [result] = select([item], 2)

        self.assertEqual(result["status"], "planning_limitation")
        self.assertIn("distinct information focuses", result["reason"])

    def test_duplicate_multi_query_focus_falls_back_when_only_preferred(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Нэг өнцөг", "  Нэг   өнцөг  "),
            preferred=("multi_angle_coverage",),
        )

        [result] = select([item], 2)

        plan = result["search_plan"]
        self.assertEqual(plan["strategy_id"], "single_query")
        self.assertTrue(plan["fallback_applied"])
        self.assertIn("distinct information focuses", plan["fallback_reason"])

    def test_search_plan_validation_rejects_empty_or_duplicate_query_text(self):
        item = proposal(
            facts=("F1", "F2"),
            focuses=("Нэг need", "Өөр need"),
        )
        base_plan = {
            "request_id": "R1",
            "strategy_id": "multi_query",
            "queries": [
                {"id": "Q1", "text": "Ижил хайлт", "targets": ["F1"]},
                {"id": "Q2", "text": "Ижил   хайлт", "targets": ["F2"]},
            ],
            "limit_per_query": 5,
            "fallback_applied": False,
            "fallback_reason": None,
        }
        with self.assertRaises(ValueError):
            selection._validate_search_plan(base_plan, item, 2)

        empty_query_plan = {
            **base_plan,
            "queries": [
                {"id": "Q1", "text": " ", "targets": ["F1"]},
                {"id": "Q2", "text": "Өөр хайлт", "targets": ["F2"]},
            ],
        }
        with self.assertRaises(ValueError):
            selection._validate_search_plan(empty_query_plan, item, 2)

    def test_rejects_invalid_ids_targets_capabilities_and_question_mapping(self):
        valid = proposal()
        invalid_values = [
            {**valid, "request_id": "R4"},
            {**valid, "required_facts": [{"id": "bad", "description": "Fact"}]},
            {**valid, "required_facts": [
                {"id": "F1", "description": "Fact 1"},
                {"id": "F1", "description": "Duplicate fact"},
            ]},
            {**valid, "information_needs": [{"id": "bad", "focus": "Need", "targets": ["F1"]}]},
            {**valid, "information_needs": [{"id": "N1", "focus": "Need", "targets": ["F2"]}]},
            {**valid, "required_capabilities": ["unknown_capability"]},
            {**valid, "required_capabilities": ["focused_lookup"], "preferred_capabilities": ["focused_lookup"]},
        ]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    select([invalid], 2)

        with self.assertRaises(ValueError):
            select([valid], 2, {"R2": "Өөр хүсэлт"})

    def test_rejects_empty_questions_needs_or_uncovered_facts(self):
        item = proposal(focuses=(" ",))
        with self.assertRaises(ValueError):
            select([item], 1)
        with self.assertRaises(ValueError):
            select([proposal()], 1, {"R1": "  "})
        with self.assertRaises(ValueError):
            select([
                proposal(
                    facts=("F1", "F2"),
                    focuses=("Нэг need",),
                    targets=(("F1",),),
                )
            ], 1)

    def test_rejects_duplicate_or_out_of_order_request_ids(self):
        with self.assertRaises(ValueError):
            select([proposal("R1"), proposal("R1")], 2)
        with self.assertRaises(ValueError):
            select([proposal("R3"), proposal("R1")], 2)

    def test_rejects_invalid_remaining_budget_and_returns_empty_for_no_proposals(self):
        for budget in (-1, True, 1.5):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    select([], budget, {})
        self.assertEqual(select([], 0, {}), [])


if __name__ == "__main__":
    unittest.main()
