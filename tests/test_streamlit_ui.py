import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

from openai import OpenAIError
from streamlit.testing.v1 import AppTest

from rag.routing import get_policy_and_route


APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


def route_result(question):
    return {
        "requests": [
            {
                "intent": "workplace",
                "search_query": question,
                "relationship": "new",
                "resolved_question": question,
            }
        ]
    }


class StreamlitUiTests(unittest.TestCase):
    question = "Нууцын баримт бичиг гэж юу вэ?"

    def setUp(self):
        self.planner_patcher = patch(
            "rag.pipeline.plan_requests", side_effect=self._offline_planner
        )
        self.planner_patcher.start()
        self.addCleanup(self.planner_patcher.stop)
        self.review_patcher = patch(
            "rag.pipeline.review_evidence",
            side_effect=self._offline_review,
        )
        self.review_patcher.start()
        self.addCleanup(self.review_patcher.stop)

    @staticmethod
    def _offline_planner(requests, *, model=None, api_key=None):
        proposals = []
        for index, request in enumerate(requests, start=1):
            if get_policy_and_route(request["intent"])["route"] != "retrieval":
                continue
            question = request["resolved_question"]
            proposals.append({
                "request_id": f"R{index}",
                "required_facts": [{"id": "F1", "description": question}],
                "information_needs": [
                    {"id": "N1", "focus": question, "targets": ["F1"]}
                ],
                "required_capabilities": ["focused_lookup"],
                "preferred_capabilities": [],
            })
        return proposals

    @staticmethod
    def _offline_review(
        question,
        passages,
        *,
        request_id,
        required_facts,
        retrieval_status,
        **kwargs,
    ):
        passage_ids = [passage["passage_id"] for passage in passages]
        supported = bool(passage_ids)
        facts = [
            {
                "fact_id": fact["id"],
                "status": "supported" if supported else "missing",
                "evidence_ids": passage_ids if supported else [],
                "missing_detail": None if supported else "Баримт олдсонгүй.",
            }
            for fact in required_facts
        ]
        return {
            "request_id": request_id,
            "review_status": "partial" if retrieval_status == "partial_error" else "complete",
            "retrieval_status": retrieval_status,
            "facts": facts,
            "selected_evidence_ids": list(dict.fromkeys(
                evidence_id for fact in facts for evidence_id in fact["evidence_ids"]
            )),
            "conflicts": [],
            "missing_information": "",
            "retry_query": "",
        }

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
            "request_id": "R1",
            "outcome": "answered",
            "blocks": [{"type": "paragraph", "items": [{
                "fact_id": "F1",
                "text": "Grounded answer",
                "evidence_ids": ["P1"],
            }]}],
            "unresolved_fact_ids": [],
            "limitation": "",
            "advice": None,
        }
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(self.question),
            ),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

        self.assertFalse(app.exception)
        self.assertIn("Grounded answer [1]", [element.value for element in app.markdown])
        self.assertTrue(any("[1] · policy.pdf" == element.value for element in app.caption))
        self.assertIn("Retrieved passage", [element.value for element in app.text])
        self.assertEqual(len(app.expander), 3)

        app.run()
        search.assert_called_once_with(self.question, limit=5)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0], self.question)
        self.assertEqual(len(generate.call_args.args[1]), 1)
        self.assertEqual(generate.call_args.args[1][0]["text"], passages[0]["text"])
        self.assertEqual(generate.call_args.args[1][0]["query_id"], "Q1")

    def test_displays_and_preserves_process_trace_in_chat_history(self):
        response = {
            "answer": "Grounded answer",
            "citations": [],
            "status": "answered",
            "passages": [],
            "process_trace": [
                {
                    "stage": "Router",
                    "status": "completed",
                    "summary": "One allowed workplace request.",
                    "details": [{"request_id": "R1", "resolved_question": self.question}],
                },
                {
                    "stage": "Retrieval",
                    "status": "completed",
                    "summary": "One query returned one passage.",
                    "details": {"passage_count": 1},
                },
            ],
        }
        with patch("ui.streamlit_ui.answer_question", return_value=response):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

            self.assertFalse(app.exception)
            self.assertTrue(
                any("Боловсруулалтын явц" in item.label for item in app.expander)
            )
            self.assertIn("**1. Router · дууссан**", [item.value for item in app.markdown])
            self.assertIn("**2. Retrieval · дууссан**", [item.value for item in app.markdown])

            app.run()

        self.assertFalse(app.exception)
        self.assertIn("**1. Router · дууссан**", [item.value for item in app.markdown])
        self.assertIn("**2. Retrieval · дууссан**", [item.value for item in app.markdown])

    def test_empty_retrieval_shows_insufficient_without_model_call(self):
        langchain_module = ModuleType("langchain_openai")
        langchain_module.ChatOpenAI = Mock()
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(self.question),
            ),
            patch("rag.pipeline.search_documents", return_value=[]),
            patch.dict("sys.modules", {"langchain_openai": langchain_module}),
        ):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value(self.question).run()

        self.assertFalse(app.exception)
        self.assertIn(
            "**Мэдээллийн хязгаарлалт:** Өгсөн баримтуудаас асуултын мэдээлэл баталгаажаагүй.",
            [element.value for element in app.info],
        )
        langchain_module.ChatOpenAI.assert_not_called()

    def test_retrieval_errors_hide_exception_details(self):
        for error in (RuntimeError("OPENAI_API_KEY=secret"), OpenAIError("secret")):
            with self.subTest(error=type(error).__name__):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result(self.question),
                    ),
                    patch("rag.pipeline.search_documents", side_effect=error),
                ):
                    app = AppTest.from_file(str(APP_FILE)).run()
                    app.chat_input[0].set_value(self.question).run()
                messages = [element.value for element in app.error]
                self.assertFalse(app.exception)
                self.assertTrue(messages)
                self.assertNotIn("secret", " ".join(messages))

    def test_generation_error_status_is_displayed_safely(self):
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(self.question),
            ),
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

    def test_routing_statuses_are_displayed_without_retrieval(self):
        cases = (
            ("salary_amount", "restricted", "warning"),
            ("general", "out_of_scope", "info"),
            ("workplace", "clarification", "info"),
            ("greeting", "greeting", "markdown"),
            ("partially_answered", "partially_answered", "markdown"),
        )
        for intent, status, element_name in cases:
            with self.subTest(status=status):
                response = {
                    "answer": f"{status} response",
                    "citations": [],
                    "status": status,
                    "passages": [],
                }
                with (
                    patch("ui.streamlit_ui.answer_question", return_value=response) as answer,
                ):
                    app = AppTest.from_file(str(APP_FILE)).run()
                    app.chat_input[0].set_value(self.question).run()

                self.assertFalse(app.exception)
                self.assertIn(
                    "greeting response" if status == "greeting" else f"{status} response",
                    [element.value for element in getattr(app, element_name)],
                )
                answer.assert_called_once_with(self.question, history=[])

    def test_streamlit_passes_full_history_with_stable_turn_ids_to_pipeline(self):
        response = {
            "answer": "Grounded answer",
            "citations": [],
            "status": "answered",
            "passages": [],
        }
        questions = [
            "Эхний асуулт",
            "Хоёр дахь асуулт",
            "Гурав дахь асуулт",
            "Дөрөв дэх асуулт",
            "Тав дахь асуулт",
        ]
        with patch("ui.streamlit_ui.answer_question", return_value=response) as answer:
            app = AppTest.from_file(str(APP_FILE)).run()
            for question in questions:
                app.chat_input[0].set_value(question).run()

        self.assertFalse(app.exception)
        self.assertEqual(answer.call_count, len(questions))
        self.assertEqual(answer.call_args_list[0].kwargs["history"], [])
        latest_history = answer.call_args_list[-1].kwargs["history"]
        self.assertEqual(len(latest_history), 2 * (len(questions) - 1))
        self.assertEqual(
            [message["content"] for message in latest_history if message["role"] == "user"],
            questions[:-1],
        )
        turn_ids = [
            latest_history[index]["turn_id"]
            for index in range(0, len(latest_history), 2)
        ]
        self.assertTrue(all(turn_ids))
        self.assertEqual(len(set(turn_ids)), len(questions) - 1)
        for index in range(0, len(latest_history), 2):
            self.assertEqual(
                latest_history[index]["turn_id"],
                latest_history[index + 1]["turn_id"],
            )
        self.assertEqual(answer.call_args_list[-1].args[0], questions[-1])

    def test_displays_grouped_answers_citations_and_passages(self):
        result = {
            "answer": "Leave answer\n\nRetention evidence is insufficient.",
            "citations": [],
            "status": "multi",
            "greeting": "Сайн байна уу!",
            "passages": [],
            "results": [
                {
                    "request_id": "R1",
                    "question": "Чөлөө яаж авах вэ?",
                    "answer": "Leave answer",
                    "citations": [{
                        "label": "1", "source_id": "R1-D1", "filename": "leave.pdf",
                        "request_id": "R1",
                    }],
                    "status": "answered",
                    "passages": [{"filename": "leave.pdf", "text": "Leave passage", "score": 0.9}],
                },
                {
                    "request_id": "R2",
                    "question": "Баримтыг хэр удаан хадгалах вэ?",
                    "answer": "Retention evidence is insufficient.",
                    "citations": [],
                    "status": "insufficient",
                    "passages": [{"filename": "security.pdf", "text": "Retention passage", "score": 0.7}],
                },
            ],
        }
        with patch("ui.streamlit_ui.answer_question", return_value=result) as answer:
            app = AppTest.from_file(str(APP_FILE)).run()
            app.chat_input[0].set_value("Хоёр асуулт").run()

            self.assertFalse(app.exception)
            self.assertIn("Сайн байна уу!", [element.value for element in app.markdown])
            self.assertIn("Leave answer", [element.value for element in app.markdown])
            self.assertIn("Retention evidence is insufficient.", [element.value for element in app.info])
            self.assertIn("[1] · R1 · leave.pdf", [element.value for element in app.caption])
            self.assertNotIn("R2-D1 · security.pdf", [element.value for element in app.caption])
            self.assertIn("Leave passage", [element.value for element in app.text])
            self.assertIn("Retention passage", [element.value for element in app.text])
            self.assertEqual(len(app.expander), 3)
            app.run()
            self.assertIn("Сайн байна уу!", [element.value for element in app.markdown])

        answer.assert_called_once_with("Хоёр асуулт", history=[])


if __name__ == "__main__":
    unittest.main()
