import unittest

from rag.answer_output import GroundingError, ground_answer, renumber_multi_request_citations


class GroundAnswerTests(unittest.TestCase):
    def setUp(self):
        self.request_id = "R1"
        self.facts = [
            {"id": "F1", "description": "Нэвтрэх эрхийн нөхцөл"},
            {"id": "F2", "description": "Хадгалах хугацаа"},
        ]
        self.passages = [
            {"passage_id": "P1", "file_id": "file_1", "filename": "policy.pdf", "text": "Нэвтрэх эрхийг хязгаарлана."},
            {"passage_id": "P2", "file_id": "file_2", "filename": "retention.pdf", "text": "Хугацааны хэсэг."},
        ]
        self.assessment = {
            "request_id": "R1",
            "retrieval_status": "success",
            "review_status": "complete",
            "facts": [
                {"fact_id": "F1", "status": "supported", "evidence_ids": ["P1"], "missing_detail": None},
                {"fact_id": "F2", "status": "missing", "evidence_ids": [], "missing_detail": "Хугацаа батлагдаагүй."},
            ],
            "conflicts": [],
            "selected_evidence_ids": ["P1"],
            "missing_information": "Хугацаа батлагдаагүй.",
            "retry_query": "",
        }
        self.decision = {
            "request_id": "R1",
            "sufficient": False,
            "supported_fact_ids": ["F1"],
            "unresolved_fact_ids": ["F2"],
            "selected_evidence_ids": ["P1"],
            "reason_codes": ["missing_support"],
        }

    def partial_draft(self):
        return {
            "request_id": "R1",
            "outcome": "partial",
            "blocks": [{
                "type": "paragraph",
                "items": [{"fact_id": "F1", "text": "Нэвтрэх эрхийг хязгаарлана.", "evidence_ids": ["P1"]}],
            }],
            "unresolved_fact_ids": ["F2"],
            "limitation": "Хадгалах хугацаа батлагдаагүй.",
            "advice": None,
        }

    def render(self, draft=None, *, facts=None, assessment=None, decision=None, passages=None):
        return ground_answer(
            self.partial_draft() if draft is None else draft,
            request_id=self.request_id,
            required_facts=self.facts if facts is None else facts,
            assessment=self.assessment if assessment is None else assessment,
            decision=self.decision if decision is None else decision,
            passages=self.passages[:1] if passages is None else passages,
        )

    def test_partial_answer_maps_selected_passage_to_trusted_metadata(self):
        result = self.render()

        self.assertEqual(result["status"], "partially_answered")
        self.assertIn("Нэвтрэх эрхийг хязгаарлана. [1]", result["answer"])
        self.assertIn("Хадгалах хугацаа батлагдаагүй.", result["answer"])
        self.assertEqual(result["citations"], [{
            "label": "1", "source_id": "D1", "passage_id": "P1",
            "file_id": "file_1", "filename": "policy.pdf",
        }])

    def test_answered_requires_every_supported_fact_and_sufficient_decision(self):
        facts = self.facts[:1]
        assessment = {
            **self.assessment,
            "facts": self.assessment["facts"][:1],
            "selected_evidence_ids": ["P1"],
        }
        decision = {
            **self.decision,
            "sufficient": True,
            "supported_fact_ids": ["F1"],
            "unresolved_fact_ids": [],
            "reason_codes": [],
        }
        draft = {
            "request_id": "R1", "outcome": "answered", "unresolved_fact_ids": [],
            "blocks": [{"type": "steps", "items": [{
                "fact_id": "F1", "text": "Нэвтрэх эрхийг хязгаарлана.", "evidence_ids": ["P1"]
            }]}], "limitation": "", "advice": None,
        }
        result = self.render(draft, facts=facts, assessment=assessment, decision=decision)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "1. Нэвтрэх эрхийг хязгаарлана. [1]")

        with self.assertRaises(GroundingError):
            self.render(
                {**draft, "blocks": []}, facts=facts, assessment=assessment, decision=decision
            )
        with self.assertRaises(GroundingError):
            self.render(draft, facts=facts, assessment=assessment, decision=self.decision)

    def test_insufficient_empty_result_has_no_citation_or_model_claim(self):
        facts = self.facts[:1]
        assessment = {
            **self.assessment,
            "facts": [{"fact_id": "F1", "status": "missing", "evidence_ids": [], "missing_detail": "Баримт олдсонгүй."}],
            "selected_evidence_ids": [],
            "missing_information": "Баримт олдсонгүй.",
        }
        decision = {
            **self.decision,
            "sufficient": False,
            "supported_fact_ids": [],
            "unresolved_fact_ids": ["F1"],
            "selected_evidence_ids": [],
        }
        draft = {
            "request_id": "R1", "outcome": "insufficient", "blocks": [],
            "unresolved_fact_ids": ["F1"], "limitation": "", "advice": None,
        }
        result = self.render(draft, facts=facts, assessment=assessment, decision=decision, passages=[])

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertIn("баримт олдсонгүй", result["answer"].casefold())

    def test_insufficient_conflicting_evidence_fails_closed_without_citations(self):
        facts = self.facts
        passages = self.passages
        assessment = {
            **self.assessment,
            "facts": [
                {"fact_id": "F1", "status": "supported", "evidence_ids": ["P1"], "missing_detail": None},
                {"fact_id": "F2", "status": "missing", "evidence_ids": [], "missing_detail": "Хугацаа тодорхойгүй."},
            ],
            "conflicts": [{"fact_id": "F1", "evidence_ids": ["P1", "P2"], "reason": "Зөрүүтэй."}],
            "selected_evidence_ids": ["P1", "P2"],
        }
        decision = {
            **self.decision,
            "sufficient": False,
            "supported_fact_ids": [],
            "unresolved_fact_ids": ["F1", "F2"],
            "selected_evidence_ids": ["P1", "P2"],
            "reason_codes": ["unresolved_conflict", "missing_support"],
        }
        draft = {
            "request_id": "R1", "outcome": "insufficient", "blocks": [],
            "unresolved_fact_ids": ["F1", "F2"],
            "limitation": "Баримтууд зөрүүтэй тул баталгаатай дүгнэлт хийх боломжгүй.",
            "advice": None,
        }

        result = self.render(
            draft, facts=facts, assessment=assessment, decision=decision, passages=passages
        )

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertNotIn("[1]", result["answer"])

    def test_empty_partial_without_supported_facts_becomes_insufficient(self):
        assessment = {
            **self.assessment,
            "facts": [{
                "fact_id": "F1", "status": "supported", "evidence_ids": ["P1"],
                "missing_detail": None,
            }, self.assessment["facts"][1]],
            "conflicts": [{
                "fact_id": "F1", "evidence_ids": ["P1", "P2"], "reason": "Зөрүүтэй."
            }],
            "selected_evidence_ids": ["P1", "P2"],
        }
        decision = {
            **self.decision,
            "supported_fact_ids": [],
            "unresolved_fact_ids": ["F1", "F2"],
            "selected_evidence_ids": ["P1", "P2"],
        }
        draft = {
            "request_id": "R1", "outcome": "partial", "blocks": [],
            "unresolved_fact_ids": ["F1", "F2"],
            "limitation": "Баримтуудын нөхцөл зөрүүтэй.", "advice": None,
        }

        result = self.render(draft, assessment=assessment, decision=decision, passages=self.passages)

        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertIn("Баримтуудын нөхцөл зөрүүтэй.", result["answer"])

    def test_empty_partial_with_supported_fact_still_fails_closed(self):
        with self.assertRaises(GroundingError):
            self.render({**self.partial_draft(), "blocks": []})

    def test_invalid_fact_passage_request_and_inline_references_fail_closed(self):
        draft = self.partial_draft()
        invalid_drafts = [
            {**draft, "request_id": "R2"},
            {**draft, "blocks": [{"type": "paragraph", "items": [{
                "fact_id": "F99", "text": "Үндэслэлгүй.", "evidence_ids": ["P1"]
            }]}]},
            {**draft, "blocks": [{"type": "paragraph", "items": [{
                "fact_id": "F1", "text": "Үндэслэлгүй.", "evidence_ids": ["P99"]
            }]}]},
            {**draft, "blocks": [{"type": "paragraph", "items": [{
                "fact_id": "F1", "text": "Хуурамч [D99] citation.", "evidence_ids": ["P1"]
            }]}]},
        ]

        for draft in invalid_drafts:
            with self.subTest(draft=draft), self.assertRaises(GroundingError):
                self.render(draft)

    def test_passage_must_be_selected_and_fact_mapped(self):
        draft = self.partial_draft()
        draft["blocks"][0]["items"][0]["evidence_ids"] = ["P2"]
        with self.assertRaises(GroundingError):
            self.render(draft, passages=self.passages)

    def test_advice_is_distinguished_from_source_grounded_claims(self):
        draft = self.partial_draft()
        draft["advice"] = {"kind": "general_guidance", "text": "Холбогдох эх сурвалжаас тодруулна уу.", "evidence_ids": []}
        self.assertIn("**Зөвлөмж:**", self.render(draft)["answer"])

        draft["advice"] = {"kind": "general_guidance", "text": "Policy гэж хэлнэ.", "evidence_ids": ["P1"]}
        with self.assertRaises(GroundingError):
            self.render(draft)


class MultiRequestCitationTests(unittest.TestCase):
    def test_citation_numbers_are_global_and_answer_markers_follow_them(self):
        results = [
            {"request_id": "R1", "answer": "Leave [1]", "citations": [{"label": "1", "passage_id": "P1"}]},
            {"request_id": "R2", "answer": "Retention [1]", "citations": [{"label": "1", "passage_id": "P1"}]},
        ]

        numbered = renumber_multi_request_citations(results)

        self.assertEqual([item["answer"] for item in numbered], ["Leave [1]", "Retention [2]"])
        self.assertEqual([item["citations"][0]["label"] for item in numbered], ["1", "2"])
        self.assertEqual([item["citations"][0]["request_id"] for item in numbered], ["R1", "R2"])


if __name__ == "__main__":
    unittest.main()
