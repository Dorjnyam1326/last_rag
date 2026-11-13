import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from rag import retrieval
from rag.retrieval import search_documents


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
        )
        self.dotenv_module.load_dotenv.assert_called_once_with(
            dotenv_path=Path(retrieval.__file__).resolve().parents[1] / ".env"
        )

    def test_default_limit_is_five_and_optional_fields_are_empty(self):
        self.search.return_value = SimpleNamespace(
            data=[SimpleNamespace(file_id="file_456", filename="notes.txt")]
        )
        with (
            patch.dict(sys.modules, self.modules),
            patch.dict(os.environ, {"OPENAI_VECTOR_STORE_ID": "vs_existing"}),
        ):
            results = search_documents("notes")

        self.assertEqual(
            results,
            [{"file_id": "file_456", "filename": "notes.txt", "text": "", "score": None}],
        )
        self.search.assert_called_once_with(
            vector_store_id="vs_existing",
            query="notes",
            max_num_results=5,
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


if __name__ == "__main__":
    unittest.main()
