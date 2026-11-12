import unittest
from unittest.mock import patch

from rag.pipeline import answer_question


class AnswerQuestionTests(unittest.TestCase):
    def test_returns_retrieval_results_for_question(self):
        expected = [
            {
                "file_id": "file_123",
                "filename": "policy.pdf",
                "text": "Passage",
                "score": 0.9,
            }
        ]
        with patch("rag.pipeline.search_documents", return_value=expected) as search:
            self.assertEqual(answer_question("Нууцын баримт бичиг гэж юу вэ?"), expected)

        search.assert_called_once_with("Нууцын баримт бичиг гэж юу вэ?", limit=5)


if __name__ == "__main__":
    unittest.main()
