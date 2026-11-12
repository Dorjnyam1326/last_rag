import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openai import OpenAIError
from streamlit.testing.v1 import AppTest


APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


class StreamlitUiTests(unittest.TestCase):
    def run_app_with_question(self, response):
        with patch("ui.streamlit_ui.answer_question", response):
            return (
                AppTest.from_file(str(APP_FILE))
                .run()
                .chat_input[0]
                .set_value("Нууцын баримт бичиг гэж юу вэ?")
                .run()
            )

    def test_displays_retrieved_passage_fields(self):
        app = self.run_app_with_question(
            lambda _: [
                {
                    "file_id": "file_123",
                    "filename": "policy.pdf",
                    "text": "Retrieved passage",
                    "score": 0.91,
                }
            ]
        )

        self.assertFalse(app.exception)
        self.assertIn("policy.pdf", [element.value for element in app.subheader])
        self.assertTrue(any("0.91" in element.value for element in app.caption))
        self.assertIn("Retrieved passage", [element.value for element in app.text])

    def test_displays_message_for_empty_results(self):
        app = self.run_app_with_question(lambda _: [])

        self.assertFalse(app.exception)
        self.assertIn("Хайлтаар тохирох баримт олдсонгүй.", [element.value for element in app.info])

    def test_hides_configuration_and_api_error_details(self):
        for error in (RuntimeError("OPENAI_API_KEY=secret"), OpenAIError("secret")):
            with self.subTest(error=type(error).__name__):
                app = self.run_app_with_question(Mock(side_effect=error))
                messages = [element.value for element in app.error]
                self.assertFalse(app.exception)
                self.assertTrue(messages)
                self.assertNotIn("secret", " ".join(messages))


if __name__ == "__main__":
    unittest.main()
