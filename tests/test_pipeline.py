import unittest
from unittest.mock import patch

from rag.pipeline import answer_question


class AnswerQuestionTests(unittest.TestCase):
    def test_retrieves_then_generates_and_returns_passages(self):
        passages = [
            {
                "file_id": "file_123",
                "filename": "policy.pdf",
                "text": "Passage",
                "score": 0.9,
            }
        ]
        generated = {"answer": "Grounded answer", "citations": [], "status": "answered"}
        with (
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question("Нууцын баримт бичиг гэж юу вэ?")

        search.assert_called_once_with("Нууцын баримт бичиг гэж юу вэ?", limit=5)
        generate.assert_called_once_with("Нууцын баримт бичиг гэж юу вэ?", passages)
        self.assertEqual(result, {**generated, "passages": passages})

    def test_empty_retrieval_is_passed_to_generation_without_extra_logic(self):
        generated = {
            "answer": "Өгсөн баримтуудаас энэ асуултад хангалттай мэдээлэл олдсонгүй.",
            "citations": [],
            "status": "insufficient",
        }
        with (
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question("Нууцын баримт бичиг гэж юу вэ?")

        search.assert_called_once()
        generate.assert_called_once_with("Нууцын баримт бичиг гэж юу вэ?", [])
        self.assertEqual(result["status"], "insufficient")

    def test_retrieval_errors_propagate_to_ui_boundary(self):
        with patch("rag.pipeline.search_documents", side_effect=RuntimeError("missing store")):
            with self.assertRaises(RuntimeError):
                answer_question("Нууцын баримт бичиг гэж юу вэ?")


if __name__ == "__main__":
    unittest.main()
