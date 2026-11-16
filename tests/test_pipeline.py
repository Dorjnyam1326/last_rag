import unittest
from unittest.mock import patch

from rag.pipeline import answer_question


def route_result(
    intent,
    question,
    search_query=None,
    relationship="new",
    resolved_question=None,
):
    return {
        "requests": [
            {
                "intent": intent,
                "search_query": question if search_query is None else search_query,
                "relationship": relationship,
                "resolved_question": question if resolved_question is None else resolved_question,
            }
        ]
    }


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.review_patcher = patch(
            "rag.pipeline.review_evidence",
            return_value={
                "status": "sufficient",
                "relevant_ids": [f"E{index}" for index in range(1, 11)],
                "missing_information": "",
                "retry_query": "",
            },
        )
        self.review_patcher.start()
        self.addCleanup(self.review_patcher.stop)

    def test_non_text_question_returns_error_without_routing(self):
        for question in (None, 123, b"question"):
            with self.subTest(question=question):
                with patch("rag.pipeline.classify_intent") as classify:
                    result = answer_question(question)

                self.assertEqual(result["status"], "error")
                self.assertEqual(result["answer"], "Асуултыг текст хэлбэрээр оруулна уу.")
                classify.assert_not_called()

    def test_empty_or_whitespace_question_returns_clarification_without_routing(self):
        for question in ("", " \t\n", "\u00a0"):
            with self.subTest(question=question):
                with patch("rag.pipeline.classify_intent") as classify:
                    result = answer_question(question)

                self.assertEqual(result["status"], "clarification")
                self.assertEqual(result["answer"], "Асуултаа бичнэ үү.")
                classify.assert_not_called()

    def test_valid_question_is_passed_to_router_unchanged(self):
        question = "  Асуулт хэвээр  "
        with patch(
            "rag.pipeline.classify_intent",
            return_value=route_result("general", question),
        ) as classify:
            result = answer_question(question)

        classify.assert_called_once_with(question, None, model=None, api_key=None)
        self.assertEqual(result["status"], "out_of_scope")

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
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        classify.assert_called_once_with(question, None)
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

        classify.assert_called_once_with(question, None)
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

    def test_follow_up_uses_resolved_context_for_search_and_generation(self):
        question = "Тэгвэл менежер байхгүй бол хэн батлах вэ?"
        history = [
            {"role": "user", "content": "Маргааш эмчид үзүүлэх чөлөөг хэрхэн авах вэ?"},
            {"role": "assistant", "content": "Чөлөө авах журмыг баримтаас шалгаж болно."},
        ]
        resolved_question = (
            "Маргааш эмчид үзүүлэх чөлөө авахад менежер байхгүй бол хэн батлах вэ?"
        )
        search_query = "Маргаашийн эмчийн үзлэгийн чөлөөг хэн батлах вэ?"
        passages = [{"file_id": "f1", "filename": "leave.pdf", "text": "passage"}]
        generated = {"answer": "Grounded", "citations": [], "status": "answered"}
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(
                    "workplace",
                    question,
                    search_query,
                    "follow_up",
                    resolved_question,
                ),
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question, history)

        classify.assert_called_once_with(question, history)
        search.assert_called_once_with(search_query, limit=5)
        generate.assert_called_once_with(resolved_question, passages)
        self.assertEqual(result["status"], "answered")

    def test_new_topic_does_not_reuse_unrelated_resolved_context(self):
        question = "Нууцын зэрэглэлтэй баримтыг хэнтэй хуваалцаж болох вэ?"
        history = [{"role": "user", "content": "Чөлөөг хэн батлах вэ?"}]
        search_query = "Нууцын зэрэглэлтэй баримтыг хэнтэй хуваалцах вэ?"
        generated = {"answer": "Grounded", "citations": [], "status": "answered"}
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(
                    "workplace", question, search_query, resolved_question=question
                ),
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            answer_question(question, history)

        classify.assert_called_once_with(question, history)
        search.assert_called_once_with(search_query, limit=5)
        generate.assert_called_once_with(question, [])

    def test_unresolved_follow_up_returns_clarification_without_search(self):
        question = "Тэгвэл яах вэ?"
        for relationship, resolved_question in (
            ("clarification", ""),
            ("follow_up", ""),
        ):
            with self.subTest(relationship=relationship):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result(
                            "workplace",
                            question,
                            search_query="",
                            relationship=relationship,
                            resolved_question=resolved_question,
                        ),
                    ),
                    patch("rag.pipeline.search_documents") as search,
                    patch("rag.pipeline.generate_answer") as generate,
                ):
                    result = answer_question(question)

                self.assertEqual(result["status"], "clarification")
                self.assertEqual(result["citations"], [])
                self.assertEqual(result["passages"], [])
                search.assert_not_called()
                generate.assert_not_called()

    def test_restricted_follow_up_is_blocked_after_context_resolution(self):
        question = "Тэгвэл миний цалин хэдэн төгрөг вэ?"
        history = [{"role": "user", "content": "Цалингийн мэдээллийн талаар асууя."}]
        route = route_result(
            "salary_amount",
            question,
            search_query="Миний цалин хэдэн төгрөг вэ?",
            relationship="follow_up",
            resolved_question=(
                "Цалингийн мэдээллийн талаар асууж байна: "
                "миний цалин хэдэн төгрөг вэ?"
            ),
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route) as classify,
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question, history)

        classify.assert_called_once_with(question, history)
        self.assertEqual(result["status"], "restricted")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["passages"], [])
        search.assert_not_called()
        generate.assert_not_called()

    def test_restricted_intents_skip_retrieval_and_generation(self):
        cases = {
            "salary_amount": "minii salary heden tugrug ve",
            "programming": "Python дээр API яаж бичих вэ?",
            "math": "2 + 2 хэд вэ?",
        }
        for intent, question in cases.items():
            with self.subTest(intent=intent):
                with (
                    patch(
                        "rag.pipeline.classify_intent",
                        return_value=route_result(intent, question, "Normalized query"),
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

        classify.assert_called_once_with(question, None)
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

    def test_missing_or_blank_search_query_falls_back_to_original_question(self):
        question = "Өглөөний ээлжийн журам юу вэ?"
        invalid_routes = (
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": question,
            },
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": question,
                "search_query": None,
            },
            {
                "intent": "workplace",
                "relationship": "new",
                "resolved_question": question,
                "search_query": "   ",
            },
        )
        for route in invalid_routes:
            with self.subTest(route=route):
                with (
                    patch("rag.pipeline.classify_intent", return_value={"requests": [route]}),
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

    def test_two_allowed_requests_keep_queries_answers_and_citations_separate(self):
        leave = {
            "intent": "workplace",
            "search_query": "Чөлөө хэрхэн авах вэ?",
            "relationship": "new",
            "resolved_question": "Чөлөө яаж авах вэ?",
        }
        retention = {
            "intent": "workplace",
            "search_query": "Нууц баримтыг хэр удаан хадгалах вэ?",
            "relationship": "new",
            "resolved_question": "Нууц баримт бичгийг хэр удаан хадгалах вэ?",
        }
        passages = [
            [{"file_id": "f1", "filename": "leave.pdf", "text": "leave"}],
            [{"file_id": "f2", "filename": "security.pdf", "text": "retention"}],
        ]
        generated = [
            {"answer": "Leave answer", "citations": [{"source_id": "D1", "filename": "leave.pdf"}], "status": "answered"},
            {"answer": "Retention answer", "citations": [{"source_id": "D1", "filename": "security.pdf"}], "status": "answered"},
        ]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": [leave, retention]}) as classify,
            patch("rag.pipeline.search_documents", side_effect=passages) as search,
            patch("rag.pipeline.generate_answer", side_effect=generated) as generate,
        ):
            result = answer_question("Чөлөө яаж авах вэ? Мөн нууц баримтыг хэр удаан хадгалах вэ?")

        classify.assert_called_once()
        self.assertEqual(search.call_args_list[0].args[0], leave["search_query"])
        self.assertEqual(search.call_args_list[1].args[0], retention["search_query"])
        self.assertEqual(generate.call_args_list[0].args, (leave["resolved_question"], passages[0]))
        self.assertEqual(generate.call_args_list[1].args, (retention["resolved_question"], passages[1]))
        self.assertEqual([item["answer"] for item in result["results"]], ["Leave answer", "Retention answer"])
        self.assertEqual([item["source_id"] for item in result["citations"]], ["R1-D1", "R2-D1"])
        self.assertEqual([item["request_id"] for item in result["passages"]], ["R1", "R2"])

    def test_allowed_and_restricted_requests_are_enforced_independently(self):
        allowed = {
            "intent": "workplace",
            "search_query": "Чөлөө авах журам",
            "relationship": "new",
            "resolved_question": "Маргааш чөлөө яаж авах вэ?",
        }
        restricted = {
            "intent": "salary_amount",
            "search_query": "Миний цалин хэд вэ?",
            "relationship": "new",
            "resolved_question": "Миний цалин хэд вэ?",
        }
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": [allowed, restricted]}),
            patch("rag.pipeline.search_documents", return_value=[{"file_id": "f1", "filename": "leave.pdf", "text": "leave"}]) as search,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Leave answer", "citations": [], "status": "answered"}) as generate,
        ):
            result = answer_question("Маргааш чөлөө яаж авах вэ? Бас миний цалин хэд вэ?")

        self.assertEqual([item["status"] for item in result["results"]], ["answered", "restricted"])
        self.assertEqual(len(result["results"][1]["passages"]), 0)
        search.assert_called_once_with(allowed["search_query"], limit=5)
        generate.assert_called_once_with(allowed["resolved_question"], search.return_value)

    def test_multiple_follow_ups_use_each_resolved_question(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "Чөлөө батлах хүн",
                "relationship": "follow_up",
                "resolved_question": "Эмчид үзүүлэх чөлөөг хэн батлах вэ?",
            },
            {
                "intent": "workplace",
                "search_query": "Чөлөө авахад шаардлагатай бичиг баримт",
                "relationship": "follow_up",
                "resolved_question": "Эмчид үзүүлэх чөлөө авахад ямар бичиг баримт хэрэгтэй вэ?",
            },
        ]
        history = [{"role": "user", "content": "Эмчид үзүүлэх чөлөө авах журмыг тайлбарла."}]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}) as classify,
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Insufficient", "citations": [], "status": "insufficient"}) as generate,
        ):
            result = answer_question("Тэгвэл хэн батлах вэ, бас ямар бичиг баримт хэрэгтэй вэ?", history)

        classify.assert_called_once_with("Тэгвэл хэн батлах вэ, бас ямар бичиг баримт хэрэгтэй вэ?", history)
        self.assertEqual(search.call_count, 2)
        self.assertEqual([call.args[0] for call in generate.call_args_list], [item["resolved_question"] for item in requests])
        self.assertEqual([item["status"] for item in result["results"]], ["insufficient", "insufficient"])

    def test_more_than_three_or_uncertain_requests_returns_clarification_without_calls(self):
        request = route_result("workplace", "Чөлөө яаж авах вэ?")["requests"][0]
        cases = (
            {"requests": [request] * 4},
            {"requests": [request, {**request, "relationship": "clarification", "resolved_question": "", "search_query": ""}]},
        )
        for routing in cases:
            with self.subTest(count=len(routing["requests"])):
                with (
                    patch("rag.pipeline.classify_intent", return_value=routing),
                    patch("rag.pipeline.search_documents") as search,
                    patch("rag.pipeline.generate_answer") as generate,
                ):
                    result = answer_question("Хэд хэдэн зүйл асууя.")

                self.assertEqual(result["status"], "clarification")
                search.assert_not_called()
                generate.assert_not_called()

    def test_one_multi_request_failure_does_not_hide_other_result(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "Нууц баримтын тодорхойлолт",
                "relationship": "new",
                "resolved_question": "Нууцын баримт бичиг гэж юу вэ?",
            },
            {
                "intent": "workplace",
                "search_query": "Чөлөө авах журам",
                "relationship": "new",
                "resolved_question": "Чөлөөг хэрхэн авах вэ?",
            },
        ]
        passages = [{"file_id": "f1", "filename": "leave.pdf", "text": "leave"}]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}),
            patch("rag.pipeline.search_documents", side_effect=[RuntimeError("secret"), passages]),
            patch("rag.pipeline.generate_answer", return_value={"answer": "Leave answer", "citations": [], "status": "answered"}) as generate,
        ):
            result = answer_question("Нууцын баримт бичиг гэж юу вэ? Мөн чөлөөг хэрхэн авах вэ?")

        self.assertEqual([item["status"] for item in result["results"]], ["error", "answered"])
        self.assertNotIn("secret", result["results"][0]["answer"])
        generate.assert_called_once_with(requests[1]["resolved_question"], passages)

    def test_generation_failure_keeps_other_request_result(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "Нууц баримтын тодорхойлолт",
                "relationship": "new",
                "resolved_question": "Нууцын баримт бичиг гэж юу вэ?",
            },
            {
                "intent": "workplace",
                "search_query": "Чөлөө авах журам",
                "relationship": "new",
                "resolved_question": "Чөлөөг хэрхэн авах вэ?",
            },
        ]
        passages = [{"file_id": "f1", "filename": "leave.pdf", "text": "leave"}]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}),
            patch("rag.pipeline.search_documents", return_value=passages),
            patch(
                "rag.pipeline.generate_answer",
                side_effect=[
                    RuntimeError("secret"),
                    {"answer": "Leave answer", "citations": [], "status": "answered"},
                ],
            ) as generate,
        ):
            result = answer_question("Нууцын баримт бичиг гэж юу вэ? Мөн чөлөөг хэрхэн авах вэ?")

        self.assertEqual([item["status"] for item in result["results"]], ["error", "answered"])
        self.assertNotIn("secret", result["results"][0]["answer"])
        self.assertEqual(generate.call_count, 2)

    def test_targeted_retry_deduplicates_and_keeps_initial_evidence(self):
        question = "Нууцын баримт бичгийг хэдэн жил хадгалах вэ?"
        first = {"file_id": "f1", "filename": "policy.pdf", "text": "Нууц баримтын ангилал."}
        second = {"file_id": "f2", "filename": "retention.pdf", "text": "Хадгалах хугацааны хүснэгт."}
        review = {
            "status": "partial",
            "relevant_ids": ["E1"],
            "missing_information": "хадгалах хугацаа",
            "retry_query": "Нууц баримт бичгийн хадгалах хугацаа",
        }
        complete_review = {
            "status": "sufficient",
            "relevant_ids": ["E1", "E2"],
            "missing_information": "",
            "retry_query": "",
        }
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [first, second]]) as search,
            patch("rag.pipeline.review_evidence", side_effect=[review, complete_review]) as evaluate,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Grounded", "citations": [], "status": "answered"}) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[1].args[0], review["retry_query"])
        self.assertEqual(evaluate.call_count, 2)
        generate.assert_called_once_with(question, [first, second])
        self.assertEqual(result["passages"], [first, second])

    def test_identical_retry_query_is_skipped_and_partial_status_is_passed(self):
        question = "Нууц баримт хадгалах хугацаа?"
        review = {
            "status": "partial",
            "relevant_ids": ["E1"],
            "missing_information": "хадгалах хугацааны тоо",
            "retry_query": "  Нууц   баримт хадгалах хугацаа? ",
        }
        passages = [{"file_id": "f1", "filename": "policy.pdf", "text": "Хугацаа тодорхойгүй."}]
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.review_evidence", return_value=review),
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        generate.assert_called_once_with(
            question,
            passages,
            evidence_status="partial",
            missing_information=review["missing_information"],
        )
        self.assertEqual(result["status"], "partially_answered")

    def test_review_failure_stops_without_generation(self):
        question = "Нууцын баримт бичиг гэж юу вэ?"
        passages = [{"file_id": "f1", "filename": "policy.pdf", "text": "passage"}]
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.review_evidence", return_value={"status": "error", "retry_query": ""}),
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["passages"], passages)
        generate.assert_not_called()

    def test_retry_search_failure_keeps_and_uses_initial_partial_evidence(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        review = {
            "status": "partial",
            "relevant_ids": ["E1"],
            "missing_information": "зөвшөөрлийн нөхцөл",
            "retry_query": "Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
        }
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[ [first], RuntimeError("retry failed") ]) as search,
            patch("rag.pipeline.review_evidence", return_value=review) as evaluate,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 2)
        evaluate.assert_called_once_with(question, [first])
        generate.assert_called_once_with(
            question,
            [first],
            evidence_status="partial",
            missing_information=review["missing_information"],
        )
        self.assertEqual(result["passages"], [first])
        self.assertEqual(result["status"], "partially_answered")

    def test_retry_review_does_not_discard_initially_relevant_evidence(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        additional = {"file_id": "f2", "filename": "other.pdf", "text": "Холбоогүй агуулга."}
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [additional]]),
            patch(
                "rag.pipeline.review_evidence",
                side_effect=[
                    {
                        "status": "partial",
                        "relevant_ids": ["E1"],
                        "missing_information": "зөвшөөрлийн нөхцөл",
                        "retry_query": "Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
                    },
                    {
                        "status": "insufficient",
                        "relevant_ids": [],
                        "missing_information": "зөвшөөрлийн нөхцөл",
                        "retry_query": "",
                    },
                ],
            ),
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        generate.assert_called_once_with(
            question,
            [first],
            evidence_status="partial",
            missing_information="зөвшөөрлийн нөхцөл",
        )
        self.assertEqual(result["passages"], [first, additional])
        self.assertEqual(result["status"], "partially_answered")

    def test_retry_review_failure_keeps_initial_partial_evidence(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        additional = {"file_id": "f2", "filename": "other.pdf", "text": "Шинэ хайлтын хэсэг."}
        initial_review = {
            "status": "partial",
            "relevant_ids": ["E1"],
            "missing_information": "зөвшөөрлийн нөхцөл",
            "retry_query": "Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
        }
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [additional]]),
            patch(
                "rag.pipeline.review_evidence",
                side_effect=[initial_review, {"status": "error", "relevant_ids": []}],
            ),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial", "citations": [], "status": "partially_answered"},
            ) as generate,
        ):
            result = answer_question(question)

        generate.assert_called_once_with(
            question,
            [first],
            evidence_status="partial",
            missing_information="зөвшөөрлийн нөхцөл",
        )
        self.assertEqual(result["passages"], [first, additional])
        self.assertEqual(result["status"], "partially_answered")


if __name__ == "__main__":
    unittest.main()
