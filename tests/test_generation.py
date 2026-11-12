import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from openai import OpenAIError

from rag import generation
from rag.generation import generate_answer


class GenerateAnswerTests(unittest.TestCase):
    def setUp(self):
        self.invoke = Mock()
        self.structured_model = Mock()
        self.structured_model.invoke = self.invoke
        self.chat = Mock()
        self.chat.with_structured_output.return_value = self.structured_model
        langchain_module = ModuleType("langchain_openai")
        langchain_module.ChatOpenAI = Mock(return_value=self.chat)
        self.langchain_module = langchain_module
        self.passages = [
            {
                "file_id": "file_1",
                "filename": "policy.pdf",
                "text": "Нууцын баримт бичигт нэвтрэх эрхийг хязгаарлана.",
                "score": 0.9,
            }
        ]

    def run_generation(self, passages):
        with (
            patch.dict(sys.modules, {"langchain_openai": self.langchain_module}),
            patch("rag.generation.load_dotenv") as load_dotenv,
            patch.dict(os.environ, {"OPENAI_MODEL": "mock-model"}),
        ):
            result = generate_answer("Нууцын баримт бичиг гэж юу вэ?", passages)
        return result, load_dotenv

    def test_relevant_evidence_returns_grounded_answer_and_matching_citation(self):
        self.invoke.return_value = SimpleNamespace(
            answer="Нэвтрэх эрхийг хязгаарласан баримт бичиг юм.",
            citations=["D1"],
            status="answered",
        )
        passages = self.passages + [
            {
                "file_id": "file_2",
                "filename": "unrelated.pdf",
                "text": "Ажлын цагийн хуваарийн мэдээлэл.",
                "score": 0.2,
            }
        ]

        result, load_dotenv = self.run_generation(passages)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "Нэвтрэх эрхийг хязгаарласан баримт бичиг юм.")
        self.assertEqual(
            result["citations"],
            [{"source_id": "D1", "file_id": "file_1", "filename": "policy.pdf"}],
        )
        self.chat.with_structured_output.assert_called_once_with(
            generation._GeneratedAnswer, method="json_schema"
        )
        self.langchain_module.ChatOpenAI.assert_called_once_with(
            model="mock-model", max_retries=0
        )
        load_dotenv.assert_called_once_with(
            dotenv_path=Path(generation.__file__).resolve().parents[1] / ".env"
        )

    def test_empty_passages_return_insufficient_without_model_call(self):
        result, load_dotenv = self.run_generation([])

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.langchain_module.ChatOpenAI.assert_not_called()
        load_dotenv.assert_not_called()

    def test_irrelevant_evidence_uses_insufficient_response(self):
        self.invoke.return_value = SimpleNamespace(
            answer="Нууцын мэдээллийг 10 жил хадгална.",
            citations=[],
            status="insufficient",
        )

        result, _ = self.run_generation(self.passages)

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertIn("Өгсөн баримтуудаас", result["answer"])
        self.assertNotIn("10 жил", result["answer"])

    def test_unknown_citation_ids_are_removed(self):
        self.invoke.return_value = SimpleNamespace(
            answer="Нэвтрэх эрхийг хязгаарлана.",
            citations=["D1", "D99"],
            status="answered",
        )

        result, _ = self.run_generation(self.passages)

        self.assertEqual([citation["source_id"] for citation in result["citations"]], ["D1"])

    def test_api_error_does_not_return_exception_details(self):
        self.invoke.side_effect = OpenAIError("OPENAI_API_KEY=secret-value")

        result, _ = self.run_generation(self.passages)

        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-value", result["answer"])
        self.assertEqual(result["citations"], [])


if __name__ == "__main__":
    unittest.main()
