import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch

from rag import retrieval
from rag.retrieval import search_documents


def search_plan(strategy_id="single_query", queries=None, limit=5):
    if queries is None:
        queries = [{"id": "Q1", "text": "policy question", "targets": ["F1"]}]
    return {
        "request_id": "R1",
        "strategy_id": strategy_id,
        "queries": queries,
        "limit_per_query": limit,
        "fallback_applied": False,
        "fallback_reason": None,
    }


class SearchDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.search = Mock(
            return_value=SimpleNamespace(
                data=[
                    SimpleNamespace(
                        file_id="file_123",
                        filename="policy.pdf",
                        score=0.92,
                        content=[
                            SimpleNamespace(type="text", text="First passage"),
                            SimpleNamespace(type="text", text="Second passage"),
                            SimpleNamespace(type="image", text="ignored"),
                        ],
                    )
                ]
            )
        )
        self.client = SimpleNamespace(
            vector_stores=SimpleNamespace(search=self.search)
        )
        openai_module = ModuleType("openai")
        openai_module.OpenAI = Mock(return_value=self.client)
        self.openai_module = openai_module
        dotenv_module = ModuleType("dotenv")
        dotenv_module.load_dotenv = Mock()
        self.dotenv_module = dotenv_module
        self.modules = {"openai": self.openai_module, "dotenv": self.dotenv_module}

    def test_search_maps_document_details_and_passes_limit(self):
        self.dotenv_module.load_dotenv.side_effect = lambda **_: os.environ.__setitem__(
            "OPENAI_VECTOR_STORE_ID", "vs_from_dotenv"
        )
        with (
            patch.dict(sys.modules, self.modules),
            patch.dict(os.environ, {}, clear=True),
        ):
            results = search_documents("leave policy", limit=3)

        self.assertEqual(
            results,
            [
                {
                    "file_id": "file_123",
                    "filename": "policy.pdf",
                    "text": "First passage\nSecond passage",
                    "score": 0.92,
                }
            ],
        )
        self.openai_module.OpenAI.assert_called_once_with(timeout=45, max_retries=0)
        self.search.assert_called_once_with(
            vector_store_id="vs_from_dotenv",
            query="leave policy",
            max_num_results=3,
            rewrite_query=False,
        )
        self.dotenv_module.load_dotenv.assert_called_once_with(
            dotenv_path=Path(retrieval.__file__).resolve().parents[1] / ".env"
        )

    def test_default_limit_is_five_and_blank_text_results_are_filtered(self):
        self.search.return_value = SimpleNamespace(
            data=[SimpleNamespace(file_id="file_456", filename="notes.txt")]
        )
        with (
            patch.dict(sys.modules, self.modules),
            patch.dict(os.environ, {"OPENAI_VECTOR_STORE_ID": "vs_existing"}),
        ):
            results = search_documents("notes")

        self.assertEqual(results, [])
        self.search.assert_called_once_with(
            vector_store_id="vs_existing",
            query="notes",
            max_num_results=5,
            rewrite_query=False,
        )

    def test_missing_store_id_fails_without_openai_call(self):
        with (
            patch.dict(sys.modules, self.modules),
            patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_VECTOR_STORE_ID"):
                search_documents("leave policy")

    def test_rejects_invalid_query_or_limit(self):
        for query, limit in ((" ", 5), ("question", 0), ("question", 51), ("question", True)):
            with self.subTest(query=query, limit=limit):
                with self.assertRaises(ValueError):
                    search_documents(query, limit)

    def test_dedup_merges_same_file_whitespace_variants_and_keeps_query_provenance(self):
        first = {
            "file_id": "file_1",
            "filename": "policy.pdf",
            "text": "One   exact\npassage",
            "score": 0.8,
            "query_id": "Q1",
            "rank": 2,
            "targets": ["F1"],
            "source_tag": "first occurrence",
        }
        repeated = {
            **first,
            "text": "One exact passage",
            "score": 0.9,
            "query_id": "Q2",
            "rank": 1,
            "targets": ["F2"],
            "source_tag": "second occurrence",
        }

        [unique] = retrieval.deduplicate_passages([first, repeated])

        self.assertEqual(unique["text"], first["text"])
        self.assertEqual(unique["query_id"], "Q1")
        self.assertEqual(unique["targets"], ["F1", "F2"])
        self.assertEqual(unique["retrieval_matches"], [
            {"query_id": "Q1", "rank": 2, "score": 0.8, "targets": ["F1"]},
            {"query_id": "Q2", "rank": 1, "score": 0.9, "targets": ["F2"]},
        ])
        self.assertEqual(
            [source["source_tag"] for source in unique["source_provenance"]],
            ["first occurrence", "second occurrence"],
        )

    def test_dedup_keeps_distinct_locations_and_versions_in_one_file(self):
        passages = [
            {"file_id": "file_1", "text": "Same text", "page_number": 1},
            {"file_id": "file_1", "text": "Same text", "page_number": 2},
            {
                "file_id": "file_1",
                "text": "Same text",
                "page_number": 1,
                "document_version": "v2",
            },
        ]

        self.assertEqual(len(retrieval.deduplicate_passages(passages)), 3)

    def test_dedup_keeps_identical_text_from_different_or_unknown_files(self):
        passages = [
            {"file_id": "file_1", "text": "Same text"},
            {"file_id": "file_2", "text": "Same text"},
            {"text": "Same text"},
            {"text": "Same text"},
        ]

        result = retrieval.deduplicate_passages(passages)

        self.assertEqual(len(result), 4)
        self.assertEqual([item["file_id"] for item in result[:2]], ["file_1", "file_2"])


class ExecuteSearchPlanTests(unittest.TestCase):
    def test_executes_single_query_and_preserves_plan_metadata(self):
        result = {"file_id": "file_1", "filename": "policy.pdf", "text": "Rule", "score": 0.9}
        with patch("rag.retrieval.search_documents", return_value=[result]) as search:
            retrieved = retrieval.execute_search_plan(search_plan(limit=4))

        search.assert_called_once_with("policy question", limit=4)
        self.assertEqual(retrieved["request_id"], "R1")
        self.assertEqual(retrieved["status"], "success")
        self.assertEqual(retrieved["searches"], [
            {"query_id": "Q1", "targets": ["F1"], "status": "success", "result_count": 1}
        ])
        self.assertEqual(retrieved["passages"], [{
            **result,
            "passage_id": "P1",
            "query_id": "Q1",
            "rank": 1,
            "targets": ["F1"],
        }])

    def test_executes_multi_query_sequentially_and_preserves_association(self):
        first = {"file_id": "file_1", "filename": "one.pdf", "text": "Shared text", "score": 0.8}
        second = {"file_id": "file_2", "filename": "two.pdf", "text": "Shared text", "score": 0.7}
        plan = search_plan(
            "multi_query",
            [
                {"id": "Q1", "text": "first focus", "targets": ["F1"]},
                {"id": "Q2", "text": "second focus", "targets": ["F2", "F3"]},
            ],
            limit=2,
        )
        with patch(
            "rag.retrieval.search_documents", side_effect=[[first], [second]]
        ) as search:
            retrieved = retrieval.execute_search_plan(plan)

        self.assertEqual(search.call_args_list, [
            call("first focus", limit=2),
            call("second focus", limit=2),
        ])
        self.assertEqual(retrieved["status"], "success")
        self.assertEqual(
            [(item["query_id"], item["targets"], item["text"]) for item in retrieved["passages"]],
            [("Q1", ["F1"], "Shared text"), ("Q2", ["F2", "F3"], "Shared text")],
        )
        self.assertEqual([item["passage_id"] for item in retrieved["passages"]], ["P1", "P2"])

    def test_empty_searches_return_empty_status(self):
        with patch("rag.retrieval.search_documents", return_value=[]) as search:
            retrieved = retrieval.execute_search_plan(search_plan())

        search.assert_called_once_with("policy question", limit=5)
        self.assertEqual(retrieved["status"], "empty")
        self.assertEqual(retrieved["searches"][0]["status"], "empty")
        self.assertEqual(retrieved["passages"], [])

    def test_one_query_failure_preserves_other_query_results(self):
        passage = {"file_id": "file_1", "filename": "policy.pdf", "text": "Rule", "score": 0.9}
        plan = search_plan("multi_query", [
            {"id": "Q1", "text": "first focus", "targets": ["F1"]},
            {"id": "Q2", "text": "second focus", "targets": ["F2"]},
        ])
        with patch(
            "rag.retrieval.search_documents",
            side_effect=[[passage], TimeoutError("provider unavailable")],
        ) as search:
            retrieved = retrieval.execute_search_plan(plan)

        self.assertEqual(search.call_count, 2)
        self.assertEqual(retrieved["status"], "partial_error")
        self.assertEqual(retrieved["searches"][1]["status"], "error")
        self.assertEqual(retrieved["searches"][1]["error_type"], "TimeoutError")
        self.assertEqual([(item["query_id"], item["targets"]) for item in retrieved["passages"]], [
            ("Q1", ["F1"])
        ])

    def test_all_query_failures_return_error_without_retry(self):
        plan = search_plan("multi_query", [
            {"id": "Q1", "text": "first focus", "targets": ["F1"]},
            {"id": "Q2", "text": "second focus", "targets": ["F2"]},
        ])
        with patch(
            "rag.retrieval.search_documents", side_effect=[RuntimeError, OSError]
        ) as search:
            retrieved = retrieval.execute_search_plan(plan)

        self.assertEqual(search.call_count, 2)
        self.assertEqual(retrieved["status"], "error")
        self.assertEqual([item["status"] for item in retrieved["searches"]], ["error", "error"])
        self.assertEqual(retrieved["passages"], [])

    def test_invalid_plans_make_no_provider_calls(self):
        valid = search_plan()
        invalid_plans = []

        plan = deepcopy(valid)
        plan["strategy_id"] = "unknown"
        invalid_plans.append(plan)
        plan = deepcopy(valid)
        plan["queries"].append({"id": "Q2", "text": "extra", "targets": ["F1"]})
        invalid_plans.append(plan)
        plan = deepcopy(valid)
        plan["queries"][0]["id"] = "Q2"
        invalid_plans.append(plan)
        plan = deepcopy(valid)
        plan["queries"][0]["text"] = "  "
        invalid_plans.append(plan)
        plan = deepcopy(valid)
        plan["queries"][0]["targets"] = ["bad-id"]
        invalid_plans.append(plan)
        plan = deepcopy(valid)
        plan["queries"][0]["targets"] = []
        invalid_plans.append(plan)
        for limit in (0, 51, True, "5"):
            plan = deepcopy(valid)
            plan["limit_per_query"] = limit
            invalid_plans.append(plan)

        with patch("rag.retrieval.search_documents") as search:
            for plan in invalid_plans:
                with self.subTest(plan=plan), self.assertRaises(ValueError):
                    retrieval.execute_search_plan(plan)

        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
