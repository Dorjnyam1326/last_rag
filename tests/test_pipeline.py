import unittest
from unittest.mock import patch

from rag.pipeline import answer_question


def route_result(intent, question, search_query=None):
    return {
        "intent": intent,
        "search_query": question if search_query is None else search_query,
    }


class AnswerQuestionTests(unittest.TestCase):
    def test_retrieves_then_generates_and_returns_passages(self):
        question = "Нууцын баримт бичиг гэж юу вэ?"
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
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        generate.assert_called_once_with(question, passages)
        self.assertEqual(result, {**generated, "passages": passages})

    def test_empty_retrieval_is_passed_to_generation_without_extra_logic(self):
        generated = {
            "answer": "Өгсөн баримтуудаас энэ асуултад хангалттай мэдээлэл олдсонгүй.",
            "citations": [],
            "status": "insufficient",
        }
        question = "Нууцын баримт бичиг гэж юу вэ?"
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question)

        search.assert_called_once()
        generate.assert_called_once_with(question, [])
        self.assertEqual(result["status"], "insufficient")

    def test_retrieval_errors_propagate_to_ui_boundary(self):
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", "Нууцын баримт бичиг гэж юу вэ?"),
            ),
            patch("rag.pipeline.search_documents", side_effect=RuntimeError("missing store")),
        ):
            with self.assertRaises(RuntimeError):
                answer_question("Нууцын баримт бичиг гэж юу вэ?")

    def test_latin_mongolian_workplace_question_is_sent_to_retrieval(self):
        question = "bi margaash emchid uzuuleh geed chuluu yaj awah ve"
        passages = [{"file_id": "f1", "filename": "leave.pdf", "text": "leave", "score": 0.8}]
        generated = {"answer": "Grounded", "citations": [], "status": "answered"}
        search_query = "Маргааш эмчид үзүүлэхийн тулд хэрхэн чөлөө авах вэ?"
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question, search_query),
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question)

        classify.assert_called_once_with(question)
        search.assert_called_once_with(search_query, limit=5)
        generate.assert_called_once_with(question, passages)
        self.assertEqual(result["status"], "answered")

    def test_salary_procedure_is_allowed_to_search(self):
        for question in (
            "Цалин нэмүүлэх хүсэлт хэрхэн гаргах вэ?",
            "Цалин хэдний өдөр буудаг вэ?",
        ):
            with self.subTest(question=question):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result("salary_procedure", question),
                    ),
                    patch("rag.pipeline.search_documents", return_value=[]) as search,
                    patch(
                        "rag.pipeline.generate_answer",
                        return_value={
                            "answer": "Insufficient",
                            "citations": [],
                            "status": "insufficient",
                        },
                    ) as generate,
                ):
                    result = answer_question(question)

                search.assert_called_once_with(question, limit=5)
                generate.assert_called_once_with(question, [])
                self.assertEqual(result["status"], "insufficient")

    def test_restricted_intents_skip_retrieval_and_generation(self):
        cases = {
            "salary_amount": "Миний цалин хэд вэ?",
            "programming": "Python дээр API яаж бичих вэ?",
            "math": "2 + 2 хэд вэ?",
        }
        for intent, question in cases.items():
            with self.subTest(intent=intent):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result(intent, question),
                    ),
                    patch("rag.pipeline.search_documents") as search,
                    patch("rag.pipeline.generate_answer") as generate,
                ):
                    result = answer_question(question)

                self.assertEqual(result["status"], "restricted")
                self.assertEqual(result["citations"], [])
                self.assertEqual(result["passages"], [])
                search.assert_not_called()
                generate.assert_not_called()

    def test_disabling_one_rule_changes_enforcement_without_changing_classifier(self):
        question = "Миний цалин хэд вэ?"
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("salary_amount", question),
            ) as classify,
            patch("rag.routing.ENABLED_RESTRICTION_IDS", {"programming", "math"}),
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch(
                "rag.pipeline.generate_answer",
                return_value={
                    "answer": "Өгсөн баримтуудаас мэдээлэл олдсонгүй.",
                    "citations": [],
                    "status": "insufficient",
                },
            ) as generate,
        ):
            result = answer_question(question)

        classify.assert_called_once_with(question)
        search.assert_called_once_with(question, limit=5)
        generate.assert_called_once_with(question, [])
        self.assertEqual(result["status"], "insufficient")

    def test_general_and_greeting_skip_retrieval_and_generation(self):
        cases = {
            "general": ("Өнөөдөр цаг агаар ямар байна вэ?", "out_of_scope"),
            "greeting": ("Сайн уу", "greeting"),
        }
        for intent, (question, expected_status) in cases.items():
            with self.subTest(intent=intent):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result(intent, question),
                    ),
                    patch("rag.pipeline.search_documents") as search,
                    patch("rag.pipeline.generate_answer") as generate,
                ):
                    result = answer_question(question)

                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["citations"], [])
                self.assertEqual(result["passages"], [])
                search.assert_not_called()
                generate.assert_not_called()

    def test_routing_failure_fails_closed_without_exposing_exception(self):
        with (
            patch("rag.pipeline.classify_intent", side_effect=RuntimeError("secret")),
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question("Миний цалин хэд вэ?")

        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret", result["answer"])
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["passages"], [])
        search.assert_not_called()
        generate.assert_not_called()

    def test_unknown_route_fails_closed(self):
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("unexpected", "Миний асуулт"),
            ),
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question("Миний асуулт")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["passages"], [])
        search.assert_not_called()
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
