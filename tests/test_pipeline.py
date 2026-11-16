import unittest
from unittest.mock import patch

from rag.pipeline import _process_request, answer_question, decide_evidence_sufficiency
from rag.answer_output import ground_answer as render_ground_answer
from rag.routing import get_enabled_restriction, get_policy_and_route
from rag.selection import select_search_plans as select_search_plans_impl


def route_result(
    intent,
    question,
    search_query=None,
    relationship="new",
    resolved_question=None,
    related_turn_ids=None,
):
    return {
        "requests": [
            {
                "intent": intent,
                "search_query": question if search_query is None else search_query,
                "relationship": relationship,
                "resolved_question": question if resolved_question is None else resolved_question,
                "related_turn_ids": [] if related_turn_ids is None else related_turn_ids,
            }
        ]
    }


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.grounder_patcher = patch(
            "rag.pipeline.ground_answer", side_effect=self._ground_generation_fixture
        )
        self.grounder_patcher.start()
        self.addCleanup(self.grounder_patcher.stop)
        self.planner_patcher = patch(
            "rag.pipeline.plan_requests", side_effect=self._offline_planner
        )
        self.planner_patcher.start()
        self.addCleanup(self.planner_patcher.stop)
        self.review_patcher = patch(
            "rag.pipeline.review_evidence",
            side_effect=self._offline_review,
        )
        self.review = self.review_patcher.start()
        self.addCleanup(self.review_patcher.stop)

    @staticmethod
    def _ground_generation_fixture(draft, **context):
        if isinstance(draft, dict) and set(draft) == {
            "request_id", "outcome", "blocks", "unresolved_fact_ids", "limitation", "advice"
        }:
            return render_ground_answer(draft, **context)

        # Existing pipeline orchestration tests use source-level Generation
        # fixtures; the actual AnswerDraft renderer has dedicated tests.
        citations = []
        passages_by_file = {
            passage.get("file_id"): passage
            for passage in context.get("passages", [])
            if isinstance(passage, dict)
        }
        for index, source in enumerate(draft.get("citations", []), start=1):
            citation = dict(source)
            passage = passages_by_file.get(citation.get("file_id"))
            citation.setdefault("label", str(index))
            if passage:
                citation.setdefault("passage_id", passage.get("passage_id"))
            citations.append(citation)
        return {
            "answer": draft.get("answer", ""),
            "citations": citations,
            "status": draft.get("status", "error"),
        }

    def assert_generation_call(self, generate, question, passages, **expected):
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args, (question, passages))
        kwargs = generate.call_args.kwargs
        for key, value in expected.items():
            self.assertEqual(kwargs[key], value)
        self.assertRegex(kwargs["request_id"], r"^R[1-9][0-9]*$")
        self.assertIsInstance(kwargs["required_facts"], list)
        self.assertIsInstance(kwargs["evidence_assessment"], dict)
        self.assertIsInstance(kwargs["evidence_decision"], dict)
        self.assertIn("model", kwargs)
        self.assertIn("api_key", kwargs)

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
                "information_needs": [{"id": "N1", "focus": question, "targets": ["F1"]}],
                "required_capabilities": ["focused_lookup"],
                "preferred_capabilities": [],
            })
        return proposals

    @staticmethod
    def _planned_passage(passage, passage_id="P1"):
        match = {"query_id": "Q1", "rank": 1, "targets": ["F1"]}
        if passage.get("score") is not None:
            match["score"] = passage["score"]
        return {
            **passage,
            "passage_id": passage_id,
            "query_id": "Q1",
            "rank": 1,
            "targets": ["F1"],
            "retrieval_matches": [match],
        }

    @classmethod
    def _assessment(
        cls,
        request_id,
        required_facts,
        passages,
        *,
        statuses=None,
        retry_query="",
        retrieval_status="success",
        review_status=None,
        missing_information="",
        conflicts=None,
    ):
        passage_ids = [passage["passage_id"] for passage in passages]
        statuses = statuses or ["supported" if passage_ids else "missing"] * len(required_facts)
        facts = []
        for index, planner_fact in enumerate(required_facts):
            status = statuses[index]
            evidence_ids = (
                list(passage_ids)
                if status == "supported"
                else passage_ids[:1]
                if status == "partial"
                else []
            )
            facts.append({
                "fact_id": planner_fact["id"],
                "status": status,
                "evidence_ids": evidence_ids,
                "missing_detail": None if status == "supported" else "Дутуу байна.",
            })
        conflicts = conflicts or []
        selected_ids = list(dict.fromkeys(
            evidence_id for fact in facts for evidence_id in fact["evidence_ids"]
        ))
        for conflict in conflicts:
            for evidence_id in conflict["evidence_ids"]:
                if evidence_id not in selected_ids:
                    selected_ids.append(evidence_id)
        return {
            "request_id": request_id,
            "review_status": review_status or (
                "partial" if retrieval_status == "partial_error" else "complete"
            ),
            "retrieval_status": retrieval_status,
            "facts": facts,
            "selected_evidence_ids": selected_ids,
            "conflicts": conflicts,
            "missing_information": missing_information,
            "retry_query": retry_query,
        }

    @classmethod
    def _offline_review(
        cls,
        question,
        passages,
        *,
        request_id,
        required_facts,
        retrieval_status,
        **kwargs,
    ):
        return cls._assessment(
            request_id,
            required_facts,
            passages,
            retrieval_status=retrieval_status,
            missing_information=(
                "Зарим хайлт амжилтгүй." if retrieval_status == "partial_error" else ""
            ),
        )

    def test_non_text_question_returns_error_without_routing(self):
        for question in (None, 123, b"question"):
            with self.subTest(question=question):
                with patch("rag.pipeline.classify_intent") as classify:
                    result = answer_question(question)

                self.assertEqual(result["status"], "error")
                self.assertEqual(result["answer"], "Асуултыг текст хэлбэрээр оруулна уу.")
                classify.assert_not_called()

    def test_evidence_decision_yes_partial_missing_and_conflict(self):
        proposal = {
            "request_id": "R1",
            "required_facts": [
                {"id": "F1", "description": "Нөхцөл нэг"},
                {"id": "F2", "description": "Нөхцөл хоёр"},
            ],
        }
        passages = [
            self._planned_passage({"file_id": "f1", "text": "Нэг"}),
            self._planned_passage({"file_id": "f2", "text": "Хоёр"}, "P2"),
        ]
        complete = self._assessment("R1", proposal["required_facts"], passages)
        self.assertEqual(
            decide_evidence_sufficiency(complete, proposal, passages),
            {
                "request_id": "R1",
                "sufficient": True,
                "supported_fact_ids": ["F1", "F2"],
                "unresolved_fact_ids": [],
                "selected_evidence_ids": ["P1", "P2"],
                "reason_codes": [],
            },
        )

        partial = self._assessment(
            "R1", proposal["required_facts"], passages,
            statuses=["partial", "missing"],
        )
        decision = decide_evidence_sufficiency(partial, proposal, passages)
        self.assertFalse(decision["sufficient"])
        self.assertEqual(decision["unresolved_fact_ids"], ["F1", "F2"])
        self.assertEqual(decision["reason_codes"], ["partial_support", "missing_support"])

        conflict = self._assessment(
            "R1", proposal["required_facts"], passages,
            conflicts=[{
                "fact_id": "F1",
                "evidence_ids": ["P1", "P2"],
                "reason": "Нөхцөл зөрүүтэй.",
            }],
        )
        decision = decide_evidence_sufficiency(conflict, proposal, passages)
        self.assertFalse(decision["sufficient"])
        self.assertEqual(decision["supported_fact_ids"], ["F2"])
        self.assertEqual(decision["unresolved_fact_ids"], ["F1"])
        self.assertEqual(decision["reason_codes"], ["unresolved_conflict"])

    def test_evidence_decision_isolated_per_request_and_partial_retrieval_is_not_yes(self):
        facts = [{"id": "F1", "description": "Нөхцөл"}]
        passages = [self._planned_passage({"file_id": "f1", "text": "Баримт"})]
        complete_proposal = {"request_id": "R1", "required_facts": facts}
        limited_proposal = {"request_id": "R2", "required_facts": facts}
        complete = self._assessment("R1", facts, passages)
        limited = self._assessment(
            "R2", facts, passages,
            retrieval_status="partial_error",
        )

        first = decide_evidence_sufficiency(complete, complete_proposal, passages)
        second = decide_evidence_sufficiency(limited, limited_proposal, passages)
        self.assertTrue(first["sufficient"])
        self.assertFalse(second["sufficient"])
        self.assertEqual(second["supported_fact_ids"], ["F1"])
        self.assertEqual(second["unresolved_fact_ids"], [])
        self.assertEqual(second["reason_codes"], ["retrieval_incomplete"])

    def test_invalid_assessments_and_reviewer_failures_raise_processing_error(self):
        proposal = {
            "request_id": "R1",
            "required_facts": [{"id": "F1", "description": "Нөхцөл"}],
        }
        passages = [self._planned_passage({"file_id": "f1", "text": "Баримт"})]
        valid = self._assessment("R1", proposal["required_facts"], passages)
        invalid = []
        missing = {**valid, "facts": [], "selected_evidence_ids": []}
        invalid.append(missing)
        duplicate = {**valid, "facts": [valid["facts"][0], valid["facts"][0]]}
        invalid.append(duplicate)
        unknown_fact = {
            **valid,
            "facts": [{**valid["facts"][0], "fact_id": "F99"}],
        }
        invalid.append(unknown_fact)
        malformed_fact = {
            **valid,
            "facts": [{**valid["facts"][0], "fact_id": "fact-one"}],
        }
        invalid.append(malformed_fact)
        unknown_evidence = {
            **valid,
            "facts": [{**valid["facts"][0], "evidence_ids": ["P9"]}],
            "selected_evidence_ids": ["P9"],
        }
        invalid.append(unknown_evidence)
        mismatch = {**valid, "request_id": "R2"}
        invalid.append(mismatch)
        for assessment in invalid:
            with self.subTest(assessment=assessment):
                with self.assertRaises(ValueError):
                    decide_evidence_sufficiency(assessment, proposal, passages)

        reviewer_failure = {**valid, "review_status": "error"}
        with self.assertRaises(ValueError):
            decide_evidence_sufficiency(reviewer_failure, proposal, passages)

    def test_evidence_decision_makes_no_model_or_retrieval_calls(self):
        proposal = {
            "request_id": "R1",
            "required_facts": [{"id": "F1", "description": "Нөхцөл"}],
        }
        passages = [self._planned_passage({"file_id": "f1", "text": "Баримт"})]
        assessment = self._assessment("R1", proposal["required_facts"], passages)
        with (
            patch("rag.pipeline.review_evidence") as reviewer,
            patch("rag.pipeline.search_documents") as search,
        ):
            decision = decide_evidence_sufficiency(assessment, proposal, passages)

        self.assertTrue(decision["sufficient"])
        reviewer.assert_not_called()
        search.assert_not_called()

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
        classify.assert_called_once_with(question, None, model=None, api_key=None)
        retrieved_passages = [self._planned_passage(passages[0])]
        self.review.assert_called_once()
        self.assertEqual(self.review.call_args.kwargs["request_id"], "R1")
        self.assertEqual(
            self.review.call_args.kwargs["required_facts"],
            [{"id": "F1", "description": question}],
        )
        self.assertEqual(
            self.review.call_args.kwargs["information_needs"],
            [{"id": "N1", "focus": question, "targets": ["F1"]}],
        )
        self.assertEqual(self.review.call_args.kwargs["retrieval_status"], "success")
        self.assertEqual(result["retry_outcome"]["reason"], "evidence_sufficient")
        self.assert_generation_call(
            generate, question, retrieved_passages, model=None, api_key=None
        )
        self.assertEqual(
            {key: value for key, value in result.items() if key != "process_trace"},
            {
                **generated,
                "passages": retrieved_passages,
                "evidence": [],
                "retrieval_status": "success",
                "retry_outcome": {
                    "request_id": "R1",
                    "status": "skipped",
                    "target_fact_ids": [],
                    "retry_query_id": None,
                    "new_passage_ids": [],
                    "search_calls_used": 0,
                    "reason": "evidence_sufficient",
                },
            },
        )
        trace = result["process_trace"]
        self.assertEqual(
            [event["stage"] for event in trace],
            [
                "Question & History guard",
                "Router",
                "Deterministic policy check",
                "Planner",
                "Select Search Strategy",
                "Retrieval",
                "Deduplication",
                "Evidence review",
                "Evidence sufficiency",
                "Targeted retry",
                "Updated evidence review",
                "Generation",
                "Grounding & citations",
            ],
        )
        self.assertEqual(trace[0]["details"]["original_question"], question)
        self.assertEqual(
            trace[4]["details"][0]["search_plan"]["queries"][0]["text"],
            question,
        )
        self.assertEqual(trace[5]["details"]["requests"][0]["passage_count"], 1)
        self.assertEqual(trace[6]["details"]["unique_passage_count"], 1)
        self.assertEqual(trace[9]["status"], "skipped")
        self.assertEqual(trace[10]["details"]["reason"], "evidence_sufficient")

    def test_pipeline_renders_answer_draft_with_cited_passage_metadata(self):
        question = "Чөлөө авах журмыг тайлбарла."
        passages = [{
            "file_id": "file_leave",
            "filename": "leave.pdf",
            "text": "Хүсэлтийг менежерт өгнө.",
        }]
        draft = {
            "request_id": "R1",
            "outcome": "answered",
            "blocks": [{"type": "paragraph", "items": [{
                "fact_id": "F1",
                "text": "Хүсэлтийг менежерт өгнө.",
                "evidence_ids": ["P1"],
            }]}],
            "unresolved_fact_ids": [],
            "limitation": "",
            "advice": None,
        }
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=passages),
            patch("rag.pipeline.generate_answer", return_value=draft) as generate,
            patch("rag.pipeline.ground_answer", wraps=render_ground_answer) as grounder,
        ):
            result = answer_question(question)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "Хүсэлтийг менежерт өгнө. [1]")
        self.assertEqual(result["citations"], [{
            "label": "1",
            "source_id": "D1",
            "passage_id": "P1",
            "file_id": "file_leave",
            "filename": "leave.pdf",
        }])
        self.assertEqual([item["passage_id"] for item in result["evidence"]], ["P1"])
        grounder.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["request_id"], "R1")
        self.assertEqual(
            generate.call_args.kwargs["evidence_decision"]["supported_fact_ids"], ["F1"]
        )

    def test_empty_partial_answer_draft_returns_insufficient_without_citations(self):
        question = "Энэ нөхцөлийг батлах нэмэлт баримт байна уу?"
        passages = [
            {"file_id": "file_a", "filename": "a.pdf", "text": "Нэг нөхцөл."},
            {"file_id": "file_b", "filename": "b.pdf", "text": "Зөрүүтэй нөхцөл."},
        ]
        planned = [
            self._planned_passage(passages[0], "P1"),
            self._planned_passage(passages[1], "P2"),
        ]
        review = self._assessment(
            "R1",
            [{"id": "F1", "description": question}],
            planned,
            statuses=["supported"],
            conflicts=[{
                "fact_id": "F1",
                "evidence_ids": ["P1", "P2"],
                "reason": "Эх сурвалжууд зөрүүтэй.",
            }],
            missing_information="Эх сурвалжуудын нөхцөл зөрүүтэй.",
        )
        draft = {
            "request_id": "R1",
            "outcome": "partial",
            "blocks": [],
            "unresolved_fact_ids": ["F1"],
            "limitation": "Эх сурвалжуудын нөхцөл зөрүүтэй.",
            "advice": None,
        }
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=passages),
            patch("rag.pipeline.review_evidence", return_value=review),
            patch("rag.pipeline.generate_answer", return_value=draft) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertNotIn("Нэг нөхцөл", result["answer"])
        self.assertEqual(result["retry_outcome"]["reason"], "unresolved_conflict_only")
        generate.assert_called_once()
        grounding = next(
            event for event in result["process_trace"]
            if event["stage"] == "Grounding & citations"
        )
        self.assertEqual(grounding["status"], "completed")
        self.assertEqual(grounding["details"]["draft_outcome"], "partial")
        self.assertEqual(grounding["details"]["status"], "insufficient")

    def test_generation_api_error_type_is_traced_but_not_returned_publicly(self):
        question = "Чөлөө авах журам юу вэ?"
        passage = {"file_id": "leave", "filename": "leave.pdf", "text": "Журам."}
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=[passage]),
            patch(
                "rag.pipeline.generate_answer",
                return_value={
                    "answer": "Хариулт үүсгэхэд OpenAI API алдаа гарлаа.",
                    "citations": [],
                    "status": "error",
                    "error_type": "RateLimitError",
                },
            ),
        ):
            result = answer_question(question)

        self.assertEqual(result["status"], "error")
        self.assertNotIn("error_type", result)
        generation = next(
            event for event in result["process_trace"] if event["stage"] == "Generation"
        )
        self.assertEqual(generation["details"]["error_type"], "RateLimitError")

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

        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[0].args[0], question)
        self.assert_generation_call(generate, question, [], model=None, api_key=None)
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["retry_outcome"]["reason"], "empty_results")

    def test_pipeline_batches_router_requests_through_planner_selector_and_retrieval(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "unused router query",
                "relationship": "new",
                "resolved_question": "Чөлөө хэрхэн авах вэ?",
            },
            {
                "intent": "salary_amount",
                "search_query": "Миний цалин хэд вэ?",
                "relationship": "new",
                "resolved_question": "Миний цалин хэд вэ?",
            },
        ]
        passage = {"file_id": "leave-file", "filename": "leave.pdf", "text": "журам"}
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}),
            patch("rag.pipeline.plan_requests", side_effect=self._offline_planner) as planner,
            patch(
                "rag.pipeline.select_search_plans",
                wraps=select_search_plans_impl,
            ) as selector,
            patch("rag.pipeline.search_documents", return_value=[passage]) as search,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Leave answer", "citations": [], "status": "answered"},
            ),
        ):
            result = answer_question("Чөлөө авах уу, бас цалин хэд вэ?")

        planner.assert_called_once_with(requests, model=None, api_key=None)
        self.assertEqual(
            selector.call_args.args[1],
            {"R1": requests[0]["resolved_question"]},
        )
        self.assertEqual(
            selector.call_args.kwargs["search_queries"],
            {"R1": requests[0]["search_query"]},
        )
        self.assertEqual(
            selector.call_args.kwargs["remaining_search_budget"], 3
        )
        search.assert_called_once_with(requests[0]["search_query"], limit=5)
        self.assertEqual(
            [item["status"] for item in result["results"]],
            ["answered", "restricted"],
        )
        self.assertEqual(result["results"][0]["passages"][0]["query_id"], "Q1")

    def test_multi_query_dedup_preserves_fact_targets_and_query_provenance(self):
        question = "Чөлөөний нөхцөл болон материал юу вэ?"
        proposal = {
            "request_id": "R1",
            "required_facts": [
                {"id": "F1", "description": "Чөлөөний нөхцөл"},
                {"id": "F2", "description": "Шаардлагатай материал"},
            ],
            "information_needs": [
                {"id": "N1", "focus": "Чөлөөний нөхцөл", "targets": ["F1"]},
                {"id": "N2", "focus": "Шаардлагатай материал", "targets": ["F2"]},
            ],
            "required_capabilities": ["focused_lookup", "multi_angle_coverage"],
            "preferred_capabilities": [],
        }
        passage = {
            "file_id": "leave-file",
            "filename": "leave.pdf",
            "text": "Чөлөөний хүсэлтийг менежерт илгээнэ.",
            "score": 0.9,
        }
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.plan_requests", return_value=[proposal]),
            patch(
                "rag.pipeline.search_documents", side_effect=[[passage], [passage]]
            ) as search,
            patch(
                "rag.pipeline.review_evidence",
                return_value=self._assessment(
                    "R1",
                    proposal["required_facts"],
                    [self._planned_passage(passage)],
                    statuses=["supported", "supported"],
                    missing_information="Материалын жагсаалт дутуу.",
                ),
            ) as review,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial", "citations": [], "status": "partially_answered"},
            ),
        ):
            answer_question(question)

        self.assertEqual(search.call_count, 2)
        self.assertEqual(review.call_count, 1)
        reviewed_passages = review.call_args.args[1]
        self.assertEqual(len(reviewed_passages), 1)
        self.assertEqual(reviewed_passages[0]["targets"], ["F1", "F2"])
        self.assertEqual(
            [match["query_id"] for match in reviewed_passages[0]["retrieval_matches"]],
            ["Q1", "Q2"],
        )
        self.assertEqual(review.call_args.kwargs["required_facts"], proposal["required_facts"])
        self.assertEqual(review.call_args.kwargs["information_needs"], proposal["information_needs"])

    def test_multi_query_plan_and_single_retry_share_the_message_budget(self):
        question = "Чөлөө батлуулахад юу шаардлагатай вэ?"
        proposal = {
            "request_id": "R1",
            "required_facts": [
                {"id": "F1", "description": "батлах нөхцөл"},
                {"id": "F2", "description": "шаардлагатай материал"},
            ],
            "information_needs": [
                {"id": "N1", "focus": "батлах нөхцөл", "targets": ["F1"]},
                {"id": "N2", "focus": "шаардлагатай материал", "targets": ["F2"]},
            ],
            "required_capabilities": ["focused_lookup", "multi_angle_coverage"],
            "preferred_capabilities": [],
        }
        passages = [
            {"file_id": "f1", "filename": "one.pdf", "text": "батлах"},
            {"file_id": "f2", "filename": "two.pdf", "text": "материал"},
            {"file_id": "f3", "filename": "three.pdf", "text": "нэмэлт нөхцөл"},
        ]
        planned_passages = [self._planned_passage(passage, f"P{index}") for index, passage in enumerate(passages, start=1)]
        partial = self._assessment(
            "R1", proposal["required_facts"], planned_passages[:1],
            statuses=["partial", "missing"],
            missing_information="нэмэлт нөхцөл",
            retry_query="Нэмэлт нөхцөлийн хайлт",
        )
        sufficient = self._assessment(
            "R1", proposal["required_facts"], planned_passages,
            statuses=["supported", "supported"],
        )
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.plan_requests", return_value=[proposal]),
            patch("rag.pipeline.search_documents", side_effect=[[passages[0]], [passages[1]], [passages[2]]]) as search,
            patch("rag.pipeline.review_evidence", side_effect=[partial, sufficient]) as review,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial grounded answer", "citations": [], "status": "partially_answered"},
            ),
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 3)
        self.assertEqual(review.call_count, 2)
        self.assertEqual(search.call_args_list[0].kwargs, {"limit": 5})
        self.assertEqual(search.call_args_list[1].kwargs, {"limit": 5})
        self.assertEqual(
            search.call_args_list[2].args[0],
            "батлах нөхцөл Дутуу байна. " + question,
        )
        self.assertEqual(result["retrieval_status"], "success")
        self.assertEqual(result["retry_outcome"]["target_fact_ids"], ["F1"])
        self.assertEqual(result["retry_outcome"]["new_passage_ids"], ["P3"])

    def test_partial_retrieval_error_reviews_successful_passages_with_limitations(self):
        question = "Чөлөөний батлах нөхцөл болон материал юу вэ?"
        proposal = {
            "request_id": "R1",
            "required_facts": [
                {"id": "F1", "description": "батлах нөхцөл"},
                {"id": "F2", "description": "шаардлагатай материал"},
            ],
            "information_needs": [
                {"id": "N1", "focus": "батлах нөхцөл", "targets": ["F1"]},
                {"id": "N2", "focus": "шаардлагатай материал", "targets": ["F2"]},
            ],
            "required_capabilities": ["focused_lookup", "multi_angle_coverage"],
            "preferred_capabilities": [],
        }
        passage = {"file_id": "f1", "filename": "leave.pdf", "text": "батлах"}
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.plan_requests", return_value=[proposal]),
            patch(
                "rag.pipeline.search_documents",
                side_effect=[[passage], RuntimeError("private provider error")],
            ) as search,
            patch(
                "rag.pipeline.review_evidence",
                return_value=self._assessment(
                    "R1",
                    proposal["required_facts"],
                    [self._planned_passage(passage)],
                    statuses=["supported", "supported"],
                    retrieval_status="partial_error",
                    missing_information="Зарим хайлт амжилтгүй.",
                ),
            ) as review,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial", "citations": [], "status": "partially_answered"},
            ) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(result["status"], "partially_answered")
        self.assertEqual(result["retrieval_status"], "partial_error")
        self.assertEqual(
            [item["status"] for item in result["searches"]], ["success", "error"]
        )
        self.assertEqual(result["passages"][0]["passage_id"], "P1")
        review.assert_called_once()
        self.assertEqual(review.call_args.kwargs["request_id"], "R1")
        self.assertEqual(review.call_args.kwargs["required_facts"], proposal["required_facts"])
        self.assertEqual(review.call_args.kwargs["retrieval_status"], "partial_error")
        self.assertEqual(result["retry_outcome"]["reason"], "retrieval_incomplete_no_unresolved_facts")
        self.assertEqual(result["retry_outcome"]["search_calls_used"], 0)
        self.assertEqual(search.call_count, 2)
        generate.assert_called_once()

    def test_retrieval_errors_return_explicit_error_without_review_or_generation(self):
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", "Нууцын баримт бичиг гэж юу вэ?"),
            ),
            patch("rag.pipeline.search_documents", side_effect=RuntimeError("missing store")),
            patch("rag.pipeline.review_evidence") as review,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question("Нууцын баримт бичиг гэж юу вэ?")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["retrieval_status"], "error")
        review.assert_not_called()
        generate.assert_not_called()

    def test_router_normalized_search_query_is_used_for_latin_retrieval(self):
        question = "bi margaash emchid uzuuleh geed chuluu yaj awah ve"
        search_query = "Маргааш эмчид үзүүлэхийн тулд хэрхэн чөлөө авах вэ?"
        passages = [{"file_id": "f1", "filename": "leave.pdf", "text": "leave", "score": 0.8}]
        generated = {"answer": "Grounded", "citations": [], "status": "answered"}
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(
                    "workplace",
                    question,
                    search_query=search_query,
                ),
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question)

        classify.assert_called_once_with(question, None, model=None, api_key=None)
        search.assert_called_once_with(search_query, limit=5)
        self.assert_generation_call(
            generate, question, [self._planned_passage(passages[0])], model=None, api_key=None
        )
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

                self.assertEqual(search.call_count, 2)
                self.assertEqual(search.call_args_list[0].args[0], question)
                self.assert_generation_call(generate, question, [], model=None, api_key=None)
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
                    related_turn_ids=["legacy-turn-0"],
                ),
            ) as classify,
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.generate_answer", return_value=generated) as generate,
        ):
            result = answer_question(question, history)

        classify.assert_called_once_with(question, history, model=None, api_key=None)
        search.assert_called_once_with(search_query, limit=5)
        self.assert_generation_call(
            generate, resolved_question, [self._planned_passage(passages[0])],
            model=None, api_key=None,
        )
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

        classify.assert_called_once_with(question, history, model=None, api_key=None)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[0].args[0], search_query)
        self.assert_generation_call(generate, question, [], model=None, api_key=None)

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

    def test_unlisted_follow_up_turn_id_returns_clarification_without_search(self):
        history = [
            {"role": "user", "turn_id": "turn-1", "content": "Чөлөөний журам?"},
            {"role": "assistant", "turn_id": "turn-1", "content": "Журам."},
        ]
        question = "Тэгвэл хэн зөвшөөрөх вэ?"
        request = route_result(
            "workplace",
            question,
            relationship="follow_up",
            resolved_question="Чөлөөг хэн зөвшөөрөх вэ?",
            related_turn_ids=["other-session-turn"],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=request),
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question, history)

        self.assertEqual(result["status"], "clarification")
        search.assert_not_called()
        generate.assert_not_called()

    def test_follow_up_reuses_passages_only_from_selected_older_turn(self):
        question = "Тэгвэл менежер байхгүй бол хэн батлах вэ?"
        selected_passage = {
            "file_id": "selected-file",
            "filename": "leave.pdf",
            "text": "Selected older turn passage",
        }
        unrelated_passage = {
            "file_id": "unrelated-file",
            "filename": "ethics.pdf",
            "text": "Unrelated recent turn passage",
        }
        new_passage = {
            "file_id": "new-file",
            "filename": "policy.pdf",
            "text": "New search result",
        }
        history = [
            {"role": "user", "turn_id": "leave-turn", "content": "3 цагийн чөлөө?"},
            {
                "role": "assistant",
                "turn_id": "leave-turn",
                "content": "Leave answer",
                "passages": [selected_passage],
            },
            {"role": "user", "turn_id": "middle-1", "content": "Өөр ажлын асуулт нэг."},
            {"role": "assistant", "turn_id": "middle-1", "content": "Өөр хариулт нэг."},
            {"role": "user", "turn_id": "middle-2", "content": "Өөр ажлын асуулт хоёр."},
            {"role": "assistant", "turn_id": "middle-2", "content": "Өөр хариулт хоёр."},
            {"role": "user", "turn_id": "ethics-turn", "content": "Ёс зүйн гомдол?"},
            {
                "role": "assistant",
                "turn_id": "ethics-turn",
                "content": "Ethics answer",
                "passages": [unrelated_passage],
            },
        ]
        request = route_result(
            "workplace",
            question,
            relationship="follow_up",
            resolved_question="3 цагийн чөлөөг менежер эзгүй үед хэн батлах вэ?",
            related_turn_ids=["leave-turn"],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=request),
            patch("rag.pipeline.search_documents", return_value=[new_passage]),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Grounded", "citations": [], "status": "answered"},
            ),
        ):
            result = answer_question(question, history)

        self.assertEqual(
            [passage["file_id"] for passage in result["passages"]],
            ["new-file", "selected-file"],
        )

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
            related_turn_ids=["legacy-turn-0"],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route) as classify,
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question, history)

        classify.assert_called_once_with(question, history, model=None, api_key=None)
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
                policy_event = next(
                    event
                    for event in result["process_trace"]
                    if event["stage"] == "Deterministic policy check"
                )
                self.assertFalse(policy_event["details"][0]["retrieval_allowed"])
                self.assertEqual(
                    next(
                        event
                        for event in result["process_trace"]
                        if event["stage"] == "Retrieval"
                    )["status"],
                    "skipped",
                )
                search.assert_not_called()
                generate.assert_not_called()

    def test_llm_route_policy_suggestions_cannot_override_restriction(self):
        question = "Миний цалин хэд вэ?"
        request = {
            "intent": "salary_amount",
            "search_query": question,
            "relationship": "new",
            "resolved_question": question,
            "policy": "allowed",
            "route": "retrieval",
        }
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": [request]}),
            patch("rag.pipeline.search_documents") as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question)

        self.assertEqual(result["status"], "restricted")
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

        classify.assert_called_once_with(question, None, model=None, api_key=None)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[0].args[0], question)
        self.assert_generation_call(generate, question, [], model=None, api_key=None)
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

    def test_missing_or_blank_legacy_search_query_uses_resolved_question(self):
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

                self.assertEqual(search.call_count, 2)
                self.assertEqual(search.call_args_list[0].args[0], question)
                self.assert_generation_call(generate, question, [], model=None, api_key=None)
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
            {
                "request_id": "R1", "outcome": "answered",
                "blocks": [{"type": "paragraph", "items": [{
                    "fact_id": "F1", "text": "Leave answer", "evidence_ids": ["P1"]
                }]}],
                "unresolved_fact_ids": [], "limitation": "", "advice": None,
            },
            {
                "request_id": "R2", "outcome": "answered",
                "blocks": [{"type": "paragraph", "items": [{
                    "fact_id": "F1", "text": "Retention answer", "evidence_ids": ["P1"]
                }]}],
                "unresolved_fact_ids": [], "limitation": "", "advice": None,
            },
        ]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": [leave, retention]}) as classify,
            patch("rag.pipeline.search_documents", side_effect=passages) as search,
            patch("rag.pipeline.generate_answer", side_effect=generated) as generate,
            patch("rag.pipeline.ground_answer", wraps=render_ground_answer),
        ):
            result = answer_question("Чөлөө яаж авах вэ? Мөн нууц баримтыг хэр удаан хадгалах вэ?")

        classify.assert_called_once()
        self.assertEqual(search.call_args_list[0].args[0], leave["search_query"])
        self.assertEqual(search.call_args_list[1].args[0], retention["search_query"])
        self.assertEqual(
            generate.call_args_list[0].args,
            (leave["resolved_question"], [self._planned_passage(passages[0][0])]),
        )
        self.assertEqual(
            generate.call_args_list[1].args,
            (retention["resolved_question"], [self._planned_passage(passages[1][0])]),
        )
        self.assertEqual(
            [item["answer"] for item in result["results"]],
            ["Leave answer [1]", "Retention answer [2]"],
        )
        self.assertEqual([item["source_id"] for item in result["citations"]], ["R1-D1", "R2-D1"])
        self.assertEqual([item["label"] for item in result["citations"]], ["1", "2"])
        self.assertEqual([item["passage_id"] for item in result["citations"]], ["P1", "P1"])
        self.assertEqual([call.kwargs["request_id"] for call in generate.call_args_list], ["R1", "R2"])
        self.assertEqual([item["request_id"] for item in result["passages"]], ["R1", "R2"])
        planner_trace = next(
            event for event in result["process_trace"] if event["stage"] == "Planner"
        )
        self.assertIn("нэг batch Planner дуудалт", planner_trace["summary"])
        self.assertEqual(
            [proposal["request_id"] for proposal in planner_trace["details"]],
            ["R1", "R2"],
        )
        self.assertEqual(
            {
                event["request_id"]
                for event in result["process_trace"]
                if event["stage"] == "Evidence review"
            },
            {"R1", "R2"},
        )

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
        self.assert_generation_call(
            generate, allowed["resolved_question"],
            [self._planned_passage(search.return_value[0])], model=None, api_key=None,
        )

    def test_multiple_follow_ups_use_each_resolved_question(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "Чөлөө батлах хүн",
                "relationship": "follow_up",
                "resolved_question": "Эмчид үзүүлэх чөлөөг хэн батлах вэ?",
                "related_turn_ids": ["legacy-turn-0"],
            },
            {
                "intent": "workplace",
                "search_query": "Чөлөө авахад шаардлагатай бичиг баримт",
                "relationship": "follow_up",
                "resolved_question": "Эмчид үзүүлэх чөлөө авахад ямар бичиг баримт хэрэгтэй вэ?",
                "related_turn_ids": ["legacy-turn-0"],
            },
        ]
        history = [{"role": "user", "content": "Эмчид үзүүлэх чөлөө авах журмыг тайлбарла."}]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}) as classify,
            patch("rag.pipeline.search_documents", return_value=[]) as search,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Insufficient", "citations": [], "status": "insufficient"}) as generate,
        ):
            result = answer_question("Тэгвэл хэн батлах вэ, бас ямар бичиг баримт хэрэгтэй вэ?", history)

        classify.assert_called_once_with(
            "Тэгвэл хэн батлах вэ, бас ямар бичиг баримт хэрэгтэй вэ?",
            history,
            model=None,
            api_key=None,
        )
        self.assertEqual(search.call_count, 4)
        self.assertEqual([call.args[0] for call in generate.call_args_list], [item["resolved_question"] for item in requests])
        self.assertEqual([item["status"] for item in result["results"]], ["insufficient", "insufficient"])

    def test_more_than_three_or_uncertain_requests_returns_clarification_without_calls(self):
        request = route_result("workplace", "Чөлөө яаж авах вэ?")["requests"][0]
        greeting = route_result("greeting", "hi")["requests"][0]
        cases = (
            {"requests": [request] * 4},
            {"requests": [greeting, {**greeting, "resolved_question": "hello"}, request, request]},
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

    def test_leading_greeting_does_not_consume_three_request_limit(self):
        question = (
            "hi, tsalin hezee buudag we, minii tsalin hed we, "
            "tsalingaa yaj nemuulj boldog we"
        )
        requests = [
            {
                "intent": "greeting",
                "search_query": "",
                "relationship": "new",
                "resolved_question": "hi",
                "related_turn_ids": [],
            },
            {
                "intent": "salary_procedure",
                "search_query": "Цалин олгох хуваарь",
                "relationship": "new",
                "resolved_question": "Цалин хэзээ олгодог вэ?",
                "related_turn_ids": [],
            },
            {
                "intent": "salary_amount",
                "search_query": "Миний цалин хэд вэ?",
                "relationship": "new",
                "resolved_question": "Миний цалин хэд вэ?",
                "related_turn_ids": [],
            },
            {
                "intent": "salary_procedure",
                "search_query": "Цалин нэмүүлэх журам",
                "relationship": "new",
                "resolved_question": "Цалингаа хэрхэн нэмүүлэх вэ?",
                "related_turn_ids": [],
            },
        ]
        passages = [
            {"file_id": "payday", "filename": "payday.pdf", "text": "15 болон сарын сүүлийн өдөр."},
            {"file_id": "raise", "filename": "raise.pdf", "text": "Цалин нэмүүлэх журам."},
        ]
        generated = [
            {"answer": "Цалин олгох хуваарь.", "citations": [], "status": "answered"},
            {"answer": "Цалин нэмүүлэх журам.", "citations": [], "status": "answered"},
        ]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}) as classify,
            patch("rag.pipeline.search_documents", side_effect=[[passages[0]], [passages[1]]]) as search,
            patch("rag.pipeline.generate_answer", side_effect=generated) as generate,
        ):
            result = answer_question(question)

        classify.assert_called_once_with(question, None, model=None, api_key=None)
        self.assertEqual(result["status"], "multi")
        self.assertEqual(result["greeting"], "Сайн байна уу! Ажлын байрны бодлого, HR эсвэл үйл ажиллагааны талаар асуугаарай.")
        self.assertTrue(result["answer"].startswith(result["greeting"]))
        self.assertEqual(
            [item["status"] for item in result["results"]],
            ["answered", "restricted", "answered"],
        )
        self.assertEqual(
            [item["answer"] for item in result["results"]],
            [
                "Цалин олгох хуваарь.",
                get_enabled_restriction("salary_amount")["response"],
                "Цалин нэмүүлэх журам.",
            ],
        )
        self.assertEqual(search.call_count, 2)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(
            next(
                event
                for event in result["process_trace"]
                if event["stage"] == "Greeting handling"
            )["details"]["substantive_request_count"],
            3,
        )

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
        self.assert_generation_call(
            generate, requests[1]["resolved_question"],
            [self._planned_passage(passages[0])], model=None, api_key=None,
        )

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
        required_facts = [{"id": "F1", "description": question}]
        review = self._assessment(
            "R1", required_facts, [self._planned_passage(first)],
            statuses=["partial"], missing_information="хадгалах хугацаа",
            retry_query="Нууц баримт бичгийн хадгалах хугацаа",
        )
        complete_review = self._assessment(
            "R1", required_facts,
            [self._planned_passage(first), self._planned_passage(second, "P2")],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [first, second]]) as search,
            patch("rag.pipeline.review_evidence", side_effect=[review, complete_review]) as evaluate,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Grounded", "citations": [], "status": "answered"}) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 2)
        expected_retry_query = f"{question} Дутуу байна."
        self.assertEqual(search.call_args_list[1].args[0], expected_retry_query)
        self.assertNotIn(review["retry_query"], expected_retry_query)
        self.assertEqual(evaluate.call_count, 2)
        merged_first = {
            **self._planned_passage(first),
            "source_provenance": [{"file_id": "f1", "filename": "policy.pdf"}],
        }
        merged_first["retrieval_matches"].append(
            {"query_id": "RQ1", "rank": 1, "targets": ["F1"]}
        )
        second_with_id = {
            **second,
            "passage_id": "P2",
            "query_id": "RQ1",
            "rank": 2,
            "targets": ["F1"],
            "retrieval_matches": [
                {"query_id": "RQ1", "rank": 2, "targets": ["F1"]}
            ],
        }
        self.assert_generation_call(
            generate, question, [merged_first, second_with_id], model=None, api_key=None
        )
        self.assertEqual(result["passages"], [merged_first, second_with_id])
        self.assertEqual(result["retry_outcome"]["status"], "performed")
        self.assertEqual(result["retry_outcome"]["retry_query_id"], "RQ1")

    def test_identical_retry_query_is_skipped_and_partial_status_is_passed(self):
        question = "Нууц баримт хадгалах хугацаа?"
        passages = [{"file_id": "f1", "filename": "policy.pdf", "text": "Хугацаа тодорхойгүй."}]
        review = self._assessment(
            "R1", [{"id": "F1", "description": question}],
            [self._planned_passage(passages[0])], statuses=["partial"],
            missing_information="хадгалах хугацааны тоо",
            retry_query="  Нууц   баримт хадгалах хугацаа? ",
        )
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result(
                    "workplace", question, search_query=f"{question} Дутуу байна."
                ),
            ),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch("rag.pipeline.review_evidence", return_value=review),
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(f"{question} Дутуу байна.", limit=5)
        self.assert_generation_call(
            generate, question, [self._planned_passage(passages[0])],
            evidence_status="partial",
            missing_information=review["missing_information"],
            model=None, api_key=None,
        )
        self.assertEqual(result["status"], "partially_answered")
        self.assertEqual(result["retry_outcome"]["status"], "skipped")
        self.assertEqual(result["retry_outcome"]["reason"], "duplicate_retry_query")

    def test_review_failure_stops_without_generation(self):
        question = "Нууцын баримт бичиг гэж юу вэ?"
        passages = [{"file_id": "f1", "filename": "policy.pdf", "text": "passage"}]
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=passages) as search,
            patch(
                "rag.pipeline.review_evidence",
                return_value={"review_status": "error", "error_type": "RateLimitError"},
            ),
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["passages"], [self._planned_passage(passages[0])])
        review_event = next(
            event for event in result["process_trace"] if event["stage"] == "Evidence review"
        )
        self.assertEqual(review_event["details"]["error_type"], "RateLimitError")
        generate.assert_not_called()

    def test_conflict_only_unresolved_fact_does_not_trigger_retry(self):
        question = "Нууц мэдээллийг ямар хэлбэрээр хадгалах вэ?"
        first = {"file_id": "f1", "filename": "policy.pdf", "text": "Цаасаар хадгална."}
        second = {"file_id": "f2", "filename": "policy2.pdf", "text": "Цахимаар хадгална."}
        assessment = self._assessment(
            "R1",
            [{"id": "F1", "description": question}],
            [self._planned_passage(first), self._planned_passage(second, "P2")],
            conflicts=[{
                "fact_id": "F1",
                "evidence_ids": ["P1", "P2"],
                "reason": "Хадгалах хэлбэр зөрүүтэй.",
            }],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", return_value=[first, second]) as search,
            patch("rag.pipeline.review_evidence", return_value=assessment),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Conflict", "citations": [], "status": "partially_answered"},
            ),
        ):
            result = answer_question(question)

        search.assert_called_once_with(question, limit=5)
        self.assertEqual(result["retry_outcome"]["status"], "skipped")
        self.assertEqual(result["retry_outcome"]["reason"], "unresolved_conflict_only")
        self.assertEqual(result["retry_outcome"]["target_fact_ids"], [])

    def test_targeted_retry_uses_only_unresolved_need_and_recomputes_decision(self):
        question = "Чөлөөг хэн батлах вэ, ямар материал хэрэгтэй вэ?"
        proposal = {
            "request_id": "R1",
            "required_facts": [
                {"id": "F1", "description": "Батлах эрх"},
                {"id": "F2", "description": "Шаардлагатай материал"},
            ],
            "information_needs": [
                {"id": "N1", "focus": "Чөлөө батлах эрх", "targets": ["F1"]},
                {"id": "N2", "focus": "Чөлөөний материалын жагсаалт", "targets": ["F2"]},
            ],
            "required_capabilities": ["focused_lookup"],
            "preferred_capabilities": [],
        }
        first = {"file_id": "f1", "filename": "leave.pdf", "text": "Менежер батална."}
        second = {"file_id": "f2", "filename": "leave.pdf", "text": "Иргэний үнэмлэх."}
        initial_review = self._assessment(
            "R1",
            proposal["required_facts"],
            [self._planned_passage(first)],
            statuses=["supported", "partial"],
            retry_query="reviewer suggestion must not drive retrieval",
        )
        initial_review["facts"][1]["missing_detail"] = "Материалын нэрс."
        updated_review = self._assessment(
            "R1",
            proposal["required_facts"],
            [self._planned_passage(first), self._planned_passage(second, "P2")],
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.plan_requests", return_value=[proposal]),
            patch("rag.pipeline.search_documents", side_effect=[[first], [second]]) as search,
            patch("rag.pipeline.review_evidence", side_effect=[initial_review, updated_review]) as review,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Grounded", "citations": [], "status": "answered"},
            ) as generate,
        ):
            result = answer_question(question)

        expected_query = "Чөлөөний материалын жагсаалт Материалын нэрс. " + question
        self.assertEqual(search.call_args_list[1].args[0], expected_query)
        self.assertNotIn(initial_review["retry_query"], expected_query)
        self.assertEqual(result["retry_outcome"]["target_fact_ids"], ["F2"])
        self.assertEqual(result["retry_outcome"]["new_passage_ids"], ["P2"])
        retry_trace = next(
            event for event in result["process_trace"] if event["stage"] == "Targeted retry"
        )
        self.assertEqual(retry_trace["details"]["query"], expected_query)
        self.assertEqual(retry_trace["details"]["duplicates_removed"], 0)
        self.assertEqual(retry_trace["details"]["unique_passage_count"], 2)
        updated_review_trace = next(
            event
            for event in result["process_trace"]
            if event["stage"] == "Updated evidence review"
        )
        self.assertEqual(updated_review_trace["status"], "completed")
        final_decision_trace = next(
            event
            for event in result["process_trace"]
            if event["stage"] == "Post-retry evidence sufficiency"
        )
        self.assertTrue(final_decision_trace["details"]["sufficient"])
        retried = result["passages"][1]
        self.assertEqual(retried["query_id"], "RQ1")
        self.assertEqual(retried["targets"], ["F2"])
        self.assertEqual(review.call_count, 2)
        self.assertEqual(review.call_args_list[1].args[1][0]["passage_id"], "P1")
        self.assertEqual(review.call_args_list[1].args[1][1]["passage_id"], "P2")
        self.assertEqual(
            review.call_args_list[1].args[1][1]["retrieval_matches"],
            [{"query_id": "RQ1", "rank": 1, "targets": ["F2"]}],
        )
        self.assert_generation_call(
            generate, question, result["passages"], model=None, api_key=None
        )
        self.assertEqual(result["status"], "answered")

    def test_targeted_retry_skips_when_shared_budget_is_exhausted(self):
        question = "Батлах эрх хэнд байна вэ?"
        proposal = {
            "request_id": "R1",
            "required_facts": [{"id": "F1", "description": question}],
            "information_needs": [{"id": "N1", "focus": "Батлах эрх", "targets": ["F1"]}],
        }
        passage = self._planned_passage(
            {"file_id": "f1", "filename": "policy.pdf", "text": "Менежер батална."}
        )
        stage = {
            "status": "planned",
            "search_plan": {"queries": [{"text": question}]},
            "retrieval": {"status": "success", "passages": [passage], "searches": []},
        }
        with (
            patch(
                "rag.pipeline.review_evidence",
                return_value=self._assessment(
                    "R1", proposal["required_facts"], [passage], statuses=["partial"]
                ),
            ),
            patch("rag.pipeline.search_documents") as search,
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial", "citations": [], "status": "partially_answered"},
            ),
        ):
            result = _process_request(
                {
                    "intent": "workplace",
                    "relationship": "new",
                    "resolved_question": question,
                },
                catch_errors=False,
                planning_proposal=proposal,
                stage_outcome=stage,
                search_budget={"remaining": 0},
            )

        search.assert_not_called()
        self.assertEqual(result["retry_outcome"]["status"], "skipped")
        self.assertEqual(result["retry_outcome"]["reason"], "search_budget_exhausted")
        self.assertEqual(result["retry_outcome"]["target_fact_ids"], ["F1"])

    def test_empty_targeted_retry_preserves_empty_status_without_second_review(self):
        question = "Чөлөөний хугацааг хэн батлах вэ?"
        with (
            patch(
                "rag.pipeline.classify_intent",
                return_value=route_result("workplace", question),
            ),
            patch("rag.pipeline.search_documents", side_effect=[[], []]) as search,
            patch("rag.pipeline.generate_answer") as generate,
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 2)
        self.review.assert_called_once()
        self.assertEqual(result["retrieval_status"], "empty")
        self.assertEqual(result["retry_outcome"]["status"], "performed")
        self.assertEqual(result["retry_outcome"]["reason"], "empty_results")
        self.assert_generation_call(generate, question, [], model=None, api_key=None)

    def test_retry_isolated_to_its_request_in_multi_request_message(self):
        requests = [
            {
                "intent": "workplace",
                "search_query": "Цалин олгох өдөр",
                "relationship": "new",
                "resolved_question": "Цалин хэзээ олгогдох вэ?",
            },
            {
                "intent": "workplace",
                "search_query": "Чөлөө батлах эрх",
                "relationship": "new",
                "resolved_question": "Чөлөөг хэн батлах вэ?",
            },
        ]
        proposals = [
            {
                "request_id": f"R{index}",
                "required_facts": [{"id": "F1", "description": request["resolved_question"]}],
                "information_needs": [
                    {"id": "N1", "focus": "Батлах эрх", "targets": ["F1"]}
                ],
                "required_capabilities": ["focused_lookup"],
                "preferred_capabilities": [],
            }
            for index, request in enumerate(requests, start=1)
        ]
        first = {"file_id": "f1", "filename": "pay.pdf", "text": "Сарын сүүлээр."}
        second = {"file_id": "f2", "filename": "leave.pdf", "text": "Менежер батална."}
        third = {"file_id": "f3", "filename": "leave2.pdf", "text": "Орлон ажилтан батална."}
        reviews = [
            self._assessment("R1", proposals[0]["required_facts"], [self._planned_passage(first)]),
            self._assessment(
                "R2", proposals[1]["required_facts"], [self._planned_passage(second)],
                statuses=["partial"],
            ),
            self._assessment(
                "R2",
                proposals[1]["required_facts"],
                [self._planned_passage(second), self._planned_passage(third, "P2")],
            ),
        ]
        with (
            patch("rag.pipeline.classify_intent", return_value={"requests": requests}),
            patch("rag.pipeline.plan_requests", return_value=proposals),
            patch("rag.pipeline.search_documents", side_effect=[[first], [second], [third]]) as search,
            patch("rag.pipeline.review_evidence", side_effect=reviews),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Answer", "citations": [], "status": "answered"},
            ),
        ):
            result = answer_question("Цалин хэзээ олгогдох вэ? Чөлөөг хэн батлах вэ?")

        self.assertEqual(search.call_count, 3)
        first_result, second_result = result["results"]
        self.assertEqual(first_result["retry_outcome"]["reason"], "evidence_sufficient")
        self.assertNotIn("RQ1", [
            match["query_id"] for match in first_result["passages"][0]["retrieval_matches"]
        ])
        self.assertEqual(second_result["retry_outcome"]["target_fact_ids"], ["F1"])
        self.assertEqual(second_result["passages"][1]["query_id"], "RQ1")

    def test_retry_search_failure_keeps_and_uses_initial_partial_evidence(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        review = self._assessment(
            "R1", [{"id": "F1", "description": question}],
            [self._planned_passage(first)], statuses=["partial"],
            missing_information="зөвшөөрлийн нөхцөл",
            retry_query="Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[ [first], RuntimeError("retry failed") ]) as search,
            patch("rag.pipeline.review_evidence", return_value=review) as evaluate,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        self.assertEqual(search.call_count, 2)
        evaluate.assert_called_once_with(
            question,
            [self._planned_passage(first)],
            request_id="R1",
            required_facts=[{"id": "F1", "description": question}],
            information_needs=[{"id": "N1", "focus": question, "targets": ["F1"]}],
            retrieval_status="success",
            model=None,
            api_key=None,
        )
        self.assert_generation_call(
            generate, question, [self._planned_passage(first)],
            evidence_status="partial",
            missing_information=review["missing_information"],
            model=None, api_key=None,
        )
        self.assertEqual(result["passages"], [self._planned_passage(first)])
        self.assertEqual(result["status"], "partially_answered")
        self.assertEqual(result["retry_outcome"]["status"], "failed")
        self.assertEqual(result["retry_outcome"]["search_calls_used"], 1)

    def test_retry_review_rechecks_combined_evidence_before_selection(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        additional = {"file_id": "f2", "filename": "other.pdf", "text": "Холбоогүй агуулга."}
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [additional]]),
            patch(
                "rag.pipeline.review_evidence",
                side_effect=[
                    self._assessment(
                        "R1", [{"id": "F1", "description": question}],
                        [self._planned_passage(first)], statuses=["partial"],
                        missing_information="зөвшөөрлийн нөхцөл",
                        retry_query="Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
                    ),
                    self._assessment(
                        "R1", [{"id": "F1", "description": question}],
                        [self._planned_passage(first), self._planned_passage(additional, "P2")],
                        statuses=["partial"], missing_information="зөвшөөрлийн нөхцөл",
                    ),
                ],
            ) as evaluate,
            patch("rag.pipeline.generate_answer", return_value={"answer": "Partial", "citations": [], "status": "partially_answered"}) as generate,
        ):
            result = answer_question(question)

        self.assert_generation_call(
            generate, question, [self._planned_passage(first)],
            evidence_status="partial",
            missing_information="зөвшөөрлийн нөхцөл",
            model=None, api_key=None,
        )
        retry_passage = {
            **additional,
            "passage_id": "P2",
            "query_id": "RQ1",
            "rank": 1,
            "targets": ["F1"],
            "retrieval_matches": [
                {"query_id": "RQ1", "rank": 1, "targets": ["F1"]}
            ],
        }
        self.assertEqual(result["passages"], [self._planned_passage(first), retry_passage])
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(
            evaluate.call_args_list[1].args[1],
            [self._planned_passage(first), retry_passage],
        )
        self.assertEqual(
            evaluate.call_args_list[1].args[1][1]["retrieval_matches"],
            [{"query_id": "RQ1", "rank": 1, "targets": ["F1"]}],
        )
        self.assertEqual(result["status"], "partially_answered")

    def test_retry_review_failure_is_a_processing_error(self):
        question = "Нууц баримтыг хэнтэй хуваалцах вэ?"
        first = {"file_id": "f1", "filename": "security.pdf", "text": "Дотоод эрх бүхий ажилтанд."}
        additional = {"file_id": "f2", "filename": "other.pdf", "text": "Шинэ хайлтын хэсэг."}
        required_facts = [{"id": "F1", "description": question}]
        initial_review = self._assessment(
            "R1", required_facts, [self._planned_passage(first)],
            statuses=["partial"], missing_information="зөвшөөрлийн нөхцөл",
            retry_query="Нууц баримт хуваалцах зөвшөөрлийн нөхцөл",
        )
        with (
            patch("rag.pipeline.classify_intent", return_value=route_result("workplace", question)),
            patch("rag.pipeline.search_documents", side_effect=[[first], [additional]]),
            patch(
                "rag.pipeline.review_evidence",
                side_effect=[initial_review, {"review_status": "error"}],
            ),
            patch(
                "rag.pipeline.generate_answer",
                return_value={"answer": "Partial", "citations": [], "status": "partially_answered"},
            ) as generate,
        ):
            result = answer_question(question)

        generate.assert_not_called()
        self.assertEqual(result["passages"][1]["query_id"], "RQ1")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["retry_outcome"]["status"], "performed")


if __name__ == "__main__":
    unittest.main()
