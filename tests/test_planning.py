import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rag.planning import plan_requests


def proposal(request_id, fact_id="F1", need_id="N1", focus="Журмын шаардлага"):
    return {
        "request_id": request_id,
        "required_facts": [{"id": fact_id, "description": "Юуг баримтаар батлах вэ?"}],
        "information_needs": [{"id": need_id, "focus": focus, "targets": [fact_id]}],
        "required_capabilities": ["focused_lookup"],
        "preferred_capabilities": [],
    }


class PlanRequestsTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.invoke = self.client.with_structured_output.return_value.invoke
        self.patches = (
            patch("rag.planning.load_dotenv"),
            patch("langchain_openai.ChatOpenAI", return_value=self.client),
        )
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_plans_one_allowed_request_in_one_structured_call(self):
        self.invoke.return_value = SimpleNamespace(proposals=[proposal("R1")])
        requests = [
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": "Чөлөө авахын тулд хаана хүсэлт гаргах вэ?",
                "search_query": "Чөлөөний хүсэлт",
                "related_turn_ids": [],
                "policy": "allowed",
                "route": "retrieval",
            }
        ]

        result = plan_requests(requests)

        self.assertEqual(result, [proposal("R1")])
        self.client.with_structured_output.assert_called_once()
        self.assertEqual(
            self.client.with_structured_output.call_args.kwargs,
            {"method": "json_schema"},
        )
        self.invoke.assert_called_once()
        planner_input = json.loads(self.invoke.call_args.args[0][1][1])
        self.assertEqual(
            planner_input["requests"],
            [
                {
                    "request_id": "R1",
                    "intent": "workplace",
                    "resolved_question": "Чөлөө авахын тулд хаана хүсэлт гаргах вэ?",
                }
            ],
        )
        self.assertNotIn("related_turn_ids", planner_input["requests"][0])
        schema = self.client.with_structured_output.call_args.args[0]
        self.assertEqual(schema.model_json_schema()["properties"]["proposals"]["type"], "array")

    def test_batches_allowed_requests_and_preserves_original_request_numbers(self):
        self.invoke.return_value = SimpleNamespace(
            proposals=[
                proposal("R1", "F1", "N1", "Чөлөө зөвшөөрөх эрх"),
                proposal("R3", "F1", "N1", "Баримт хадгалах хугацаа"),
            ]
        )
        requests = [
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": "Чөлөөг хэн зөвшөөрөх вэ?",
            },
            {
                "intent": "salary_amount",
                "relationship": "new",
                "resolved_question": "Миний цалин хэд вэ?",
                "policy": "allowed",
                "route": "retrieval",
            },
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": "Баримтыг хэдий хугацаанд хадгалах вэ?",
            },
        ]

        result = plan_requests(requests)

        self.assertEqual([item["request_id"] for item in result], ["R1", "R3"])
        self.assertEqual(result[0]["information_needs"][0]["focus"], "Чөлөө зөвшөөрөх эрх")
        self.assertEqual(result[1]["information_needs"][0]["focus"], "Баримт хадгалах хугацаа")
        self.invoke.assert_called_once()
        planner_input = json.loads(self.invoke.call_args.args[0][1][1])
        self.assertEqual(
            [item["request_id"] for item in planner_input["requests"]], ["R1", "R3"]
        )
        self.assertNotIn("Миний цалин хэд вэ?", self.invoke.call_args.args[0][1][1])

    def test_keeps_multiple_required_facts_and_need_targets_in_one_request(self):
        self.invoke.return_value = SimpleNamespace(
            proposals=[
                {
                    "request_id": "R1",
                    "required_facts": [
                        {"id": "F1", "description": "Хэн орлон зөвшөөрөх вэ?"},
                        {"id": "F2", "description": "Хүсэлтийг хаана гаргах вэ?"},
                    ],
                    "information_needs": [
                        {"id": "N1", "focus": "Орлон зөвшөөрөх эрх", "targets": ["F1"]},
                        {"id": "N2", "focus": "Чөлөө бүртгэх журам", "targets": ["F2"]},
                    ],
                    "required_capabilities": ["focused_lookup"],
                    "preferred_capabilities": ["multi_angle_coverage"],
                }
            ]
        )
        requests = [
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": "Менежер эзгүй үед хэн зөвшөөрөх, хүсэлтээ хаана гаргах вэ?",
            }
        ]

        result = plan_requests(requests)

        self.assertEqual(len(result[0]["required_facts"]), 2)
        self.assertEqual(
            [need["targets"] for need in result[0]["information_needs"]],
            [["F1"], ["F2"]],
        )

    def test_does_not_call_model_when_no_allowed_retrieval_request_exists(self):
        result = plan_requests(
            [
                {
                    "intent": "salary_amount",
                    "relationship": "new",
                    "resolved_question": "Миний цалин хэд вэ?",
                }
            ]
        )

        self.assertEqual(result, [])
        self.client.with_structured_output.assert_not_called()

    def test_rejects_invalid_request_and_fact_need_capability_contracts(self):
        request = {
            "intent": "workplace",
            "relationship": "new",
            "resolved_question": "Чөлөө авах журам?",
        }
        invalid_proposals = (
            proposal("R2"),
            {**proposal("R1"), "route": "retrieval"},
            {**proposal("R1"), "required_facts": [{"id": "F1", "description": "Fact"}, {"id": "F1", "description": "Duplicate"}]},
            {**proposal("R1"), "information_needs": [{"id": "N1", "focus": "Need", "targets": ["F9"]}]},
            {**proposal("R1"), "required_capabilities": ["unsupported_capability"]},
            {**proposal("R1"), "required_capabilities": ["focused_lookup"], "preferred_capabilities": ["focused_lookup"]},
        )
        for invalid in invalid_proposals:
            with self.subTest(invalid=invalid):
                self.invoke.return_value = SimpleNamespace(proposals=[invalid])
                with self.assertRaises((ValueError, TypeError)):
                    plan_requests([request])

    def test_rejects_duplicate_and_unknown_fact_or_need_ids(self):
        request = {
            "intent": "workplace",
            "relationship": "new",
            "resolved_question": "Чөлөө авах журам?",
        }
        for invalid in (
            {
                **proposal("R1"),
                "required_facts": [
                    {"id": "F1", "description": "Fact one"},
                    {"id": "F2", "description": "Fact two"},
                ],
                "information_needs": [
                    {"id": "N1", "focus": "Need one", "targets": ["F1"]},
                    {"id": "N1", "focus": "Need two", "targets": ["F2"]},
                ],
            },
            {**proposal("R1"), "required_facts": [{"id": "bad", "description": "Fact"}]},
            {**proposal("R1"), "information_needs": [{"id": "bad", "focus": "Need", "targets": ["F1"]}]},
        ):
            with self.subTest(invalid=invalid):
                self.invoke.return_value = SimpleNamespace(proposals=[invalid])
                with self.assertRaises((ValueError, TypeError)):
                    plan_requests([request])


if __name__ == "__main__":
    unittest.main()
