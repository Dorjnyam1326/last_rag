import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

from openai import OpenAIError
from streamlit.testing.v1 import AppTest


APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


class StreamlitUiTests(unittest.TestCase):
    question = "Нууцын баримт бичиг гэж юу вэ?"

    def test_displays_answer_citations_and_passages_without_duplicate_calls(self):
        passages = [
            {
                "file_id": "file_123",
                "filename": "policy.pdf",
                "text": "Retrieved passage",
                "score": 0.91,
            }
        ]
        generated = {
            "answer": "Grounded answer",
            "citations": [{"source_id": "D1", "file_id": "file_123", "filename": "policy.pdf"}],
            "status": "answered",
        }
        with (
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

        self.assertFalse(app.exception)
        self.assertIn("Grounded answer", [element.value for element in app.markdown])
        self.assertTrue(any("D1 · policy.pdf" == element.value for element in app.caption))
        self.assertIn("Retrieved passage", [element.value for element in app.text])
        self.assertEqual(len(app.expander), 1)

        app.run()
        search.assert_called_once_with(self.question, limit=5)
        generate.assert_called_once_with(self.question, passages)

    def test_empty_retrieval_shows_insufficient_without_model_call(self):
        langchain_module = ModuleType("langchain_openai")
        langchain_module.ChatOpenAI = Mock()
        with (
            patch("rag.pipeline.search_documents", return_value=[]),
            patch.dict("sys.modules", {"langchain_openai": langchain_module}),
        ):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

        self.assertFalse(app.exception)
        self.assertIn("Өгсөн баримтуудаас энэ асуултад хангалттай мэдээлэл олдсонгүй.", [element.value for element in app.info])
        langchain_module.ChatOpenAI.assert_not_called()

    def test_retrieval_errors_hide_exception_details(self):
        for error in (RuntimeError("OPENAI_API_KEY=secret"), OpenAIError("secret")):
            with self.subTest(error=type(error).__name__):
                with patch("rag.pipeline.search_documents", side_effect=error):
                    app = AppTest.from_file(str(APP_FILE)).run()
                    app.chat_input[0].set_value(self.question).run()
                messages = [element.value for element in app.error]
                self.assertFalse(app.exception)
                self.assertTrue(messages)
                self.assertNotIn("secret", " ".join(messages))

    def test_generation_error_status_is_displayed_safely(self):
        with (
            patch("rag.pipeline.search_documents", return_value=[]),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Safe error", "citations": [], "status": "error"},
            ),
        ):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

        self.assertFalse(app.exception)
        self.assertIn("Safe error", [element.value for element in app.error])


if __name__ == "__main__":
    unittest.main()
