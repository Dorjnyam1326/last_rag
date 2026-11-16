import re


class GroundingError(ValueError):
    """Raised when an answer draft cannot be safely tied to selected evidence."""


_REQUEST_ID = re.compile(r"R[1-9][0-9]*\Z")
_FACT_ID = re.compile(r"F[1-9][0-9]*\Z")
_PASSAGE_ID = re.compile(r"P[1-9][0-9]*\Z")
_CITATION_MARKER = re.compile(r"\[(?:P[1-9][0-9]*|D[1-9][0-9]*|[1-9][0-9]*)\]")
_NUMBERED_CITATION = re.compile(r"\[([1-9][0-9]*)\]")


def ground_answer(
    draft: dict,
    *,
    request_id: str,
    required_facts: list[dict],
    assessment: dict,
    decision: dict,
    passages: list[dict],
) -> dict:
    """Validate a structured draft and render trusted passage citations."""
    passage_registry = _validate_context(
        request_id, required_facts, assessment, decision, passages
    )
    if not isinstance(draft, dict) or set(draft) != {
        "request_id", "outcome", "blocks", "unresolved_fact_ids", "limitation", "advice"
    } or draft["request_id"] != request_id:
        raise GroundingError("answer draft has an invalid request or shape")

    outcome = draft["outcome"]
    unresolved = decision["unresolved_fact_ids"]
    if (
        outcome not in {"answered", "partial", "insufficient"}
        or draft["unresolved_fact_ids"] != unresolved
        or not isinstance(draft["limitation"], str)
        or _CITATION_MARKER.search(draft["limitation"])
    ):
        raise GroundingError("answer draft outcome is inconsistent")

    facts = {fact["id"]: fact for fact in required_facts}
    assessments = {fact["fact_id"]: fact for fact in assessment["facts"]}
    conflicts = {conflict["fact_id"] for conflict in assessment["conflicts"]}
    supported_ids = set(decision["supported_fact_ids"])
    unresolved_ids = set(unresolved)
    if (
        outcome == "partial"
        and draft["blocks"] == []
        and not decision["sufficient"]
        and not supported_ids
        and unresolved_ids == set(facts)
    ):
        outcome = "insufficient"
    partial_ids = {
        fact_id
        for fact_id, fact in assessments.items()
        if fact["status"] == "partial" and fact_id not in conflicts
    }

    if outcome == "answered":
        if not decision["sufficient"] or unresolved_ids or draft["limitation"].strip():
            raise GroundingError("answered draft conflicts with evidence decision")
    elif outcome == "partial":
        if decision["sufficient"] or not draft["limitation"].strip():
            raise GroundingError("partial draft conflicts with evidence decision")
        if not supported_ids and not partial_ids:
            raise GroundingError("insufficient evidence cannot be rendered as partial")
    else:
        if (
            decision["sufficient"]
            or supported_ids
            or draft["blocks"]
            or set(unresolved) != set(facts)
        ):
            raise GroundingError("insufficient draft conflicts with evidence decision")

    if not isinstance(draft["blocks"], list):
        raise GroundingError("answer draft blocks must be a list")

    source_ids = {
        passage_id: f"D{index}"
        for index, passage in enumerate(passages, start=1)
        if (passage_id := passage["passage_id"])
    }
    labels = {}
    citations = []
    rendered_blocks = []
    covered_facts = set()

    def citation_labels(evidence_ids: list[str]) -> str:
        rendered = []
        for evidence_id in evidence_ids:
            if evidence_id not in labels:
                passage = passage_registry[evidence_id]
                label = str(len(labels) + 1)
                labels[evidence_id] = label
                citations.append({
                    "label": label,
                    "source_id": source_ids[evidence_id],
                    "passage_id": evidence_id,
                    "file_id": passage["file_id"],
                    "filename": passage["filename"],
                })
            rendered.append(f"[{labels[evidence_id]}]")
        return "".join(rendered)

    for block in draft["blocks"]:
        if (
            not isinstance(block, dict)
            or set(block) != {"type", "items"}
            or block["type"] not in {"paragraph", "bullets", "steps"}
            or not isinstance(block["items"], list)
            or not block["items"]
        ):
            raise GroundingError("answer draft contains an invalid block")
        rendered_items = []
        for item in block["items"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"fact_id", "text", "evidence_ids"}
            ):
                raise GroundingError("answer draft contains an invalid item")
            fact_id = item["fact_id"]
            text = item["text"]
            evidence_ids = item["evidence_ids"]
            fact_assessment = assessments.get(fact_id)
            if (
                fact_id not in facts
                or fact_id in conflicts
                or not isinstance(text, str)
                or not text.strip()
                or _CITATION_MARKER.search(text)
                or not isinstance(evidence_ids, list)
                or not evidence_ids
                or len(set(evidence_ids)) != len(evidence_ids)
                or fact_assessment is None
                or fact_assessment["status"] not in {"supported", "partial"}
                or (fact_assessment["status"] == "partial" and outcome != "partial")
                or any(
                    evidence_id not in passage_registry
                    or evidence_id not in fact_assessment["evidence_ids"]
                    for evidence_id in evidence_ids
                )
            ):
                raise GroundingError("answer item references unsupported evidence")

            covered_facts.add(fact_id)
            text = " ".join(text.split()) + " " + citation_labels(evidence_ids)
            if block["type"] == "bullets":
                rendered_items.append(f"- {text}")
            elif block["type"] == "steps":
                rendered_items.append(f"{len(rendered_items) + 1}. {text}")
            else:
                rendered_items.append(text)
        rendered_blocks.append("\n".join(rendered_items))

    if not supported_ids.issubset(covered_facts):
        raise GroundingError("answer draft omits a supported required fact")
    if outcome == "answered" and covered_facts != set(facts):
        raise GroundingError("answered draft does not cover every required fact")
    if outcome == "partial" and not covered_facts.intersection(supported_ids | partial_ids):
        raise GroundingError("partial draft contains no supported factual information")

    advice = draft["advice"]
    if advice is not None:
        if (
            not isinstance(advice, dict)
            or set(advice) != {"kind", "text", "evidence_ids"}
            or advice["kind"] not in {"source_grounded", "general_guidance"}
            or not isinstance(advice["text"], str)
            or not advice["text"].strip()
            or _CITATION_MARKER.search(advice["text"])
            or not isinstance(advice["evidence_ids"], list)
            or len(set(advice["evidence_ids"])) != len(advice["evidence_ids"])
        ):
            raise GroundingError("answer draft contains invalid advice")
        if advice["kind"] == "general_guidance" and advice["evidence_ids"]:
            raise GroundingError("general advice cannot claim source evidence")
        if advice["kind"] == "source_grounded" and not advice["evidence_ids"]:
            raise GroundingError("source-grounded advice requires evidence")
        if any(evidence_id not in passage_registry for evidence_id in advice["evidence_ids"]):
            raise GroundingError("advice references unavailable evidence")
        advice_text = "**Зөвлөмж:** " + " ".join(advice["text"].split())
        if advice["kind"] == "source_grounded":
            advice_text += " " + citation_labels(advice["evidence_ids"])
        rendered_blocks.append(advice_text)

    if outcome in {"partial", "insufficient"}:
        limitation = " ".join(draft["limitation"].split()) or assessment.get("missing_information", "")
        if not limitation:
            limitation = "Өгсөн баримтаар асуултын бүх хэсгийг баталгаажуулж чадсангүй."
        rendered_blocks.append(f"**Мэдээллийн хязгаарлалт:** {limitation}")

    return {
        "answer": "\n\n".join(rendered_blocks),
        "citations": citations,
        "status": {
            "answered": "answered",
            "partial": "partially_answered",
            "insufficient": "insufficient",
        }[outcome],
    }


def renumber_multi_request_citations(results: list[dict]) -> list[dict]:
    """Assign stable citation labels across one aggregated multi-request answer."""
    next_label = 1
    grounded_results = []
    for item in results:
        result = {**item}
        request_id = result.get("request_id")
        label_map = {}
        citations = []
        for citation in result.get("citations", []):
            old_label = citation["label"]
            citation = {**citation}
            citation["label"] = str(next_label)
            citation["request_id"] = request_id
            label_map[old_label] = str(next_label)
            citations.append(citation)
            next_label += 1

        answer = result["answer"]

        def replace_label(match):
            label = label_map.get(match.group(1))
            if label is None:
                raise GroundingError("rendered answer contains an unresolved citation")
            return f"[{label}]"

        result["answer"] = _NUMBERED_CITATION.sub(replace_label, answer)
        result["citations"] = citations
        grounded_results.append(result)
    return grounded_results


def _validate_context(
    request_id: str,
    required_facts: list[dict],
    assessment: dict,
    decision: dict,
    passages: list[dict],
) -> dict[str, dict]:
    if (
        not isinstance(request_id, str)
        or not _REQUEST_ID.fullmatch(request_id)
        or not isinstance(required_facts, list)
        or not required_facts
        or not isinstance(assessment, dict)
        or assessment.get("request_id") != request_id
        or not isinstance(decision, dict)
        or decision.get("request_id") != request_id
        or not isinstance(passages, list)
    ):
        raise GroundingError("grounding context is invalid")

    fact_ids = []
    for fact in required_facts:
        if (
            not isinstance(fact, dict)
            or set(fact) != {"id", "description"}
            or not isinstance(fact.get("id"), str)
            or not _FACT_ID.fullmatch(fact["id"])
            or fact["id"] in fact_ids
            or not isinstance(fact.get("description"), str)
            or not fact["description"].strip()
        ):
            raise GroundingError("grounding facts are invalid")
        fact_ids.append(fact["id"])

    passage_registry = {}
    for passage in passages:
        if (
            not isinstance(passage, dict)
            or not isinstance(passage.get("passage_id"), str)
            or not _PASSAGE_ID.fullmatch(passage["passage_id"])
            or passage["passage_id"] in passage_registry
            or not isinstance(passage.get("text"), str)
            or not passage["text"].strip()
            or not isinstance(passage.get("file_id"), str)
            or not passage["file_id"].strip()
            or not isinstance(passage.get("filename"), str)
            or not passage["filename"].strip()
        ):
            raise GroundingError("evidence registry contains an invalid passage")
        passage_registry[passage["passage_id"]] = passage

    if (
        not isinstance(assessment.get("facts"), list)
        or not isinstance(assessment.get("conflicts"), list)
        or not isinstance(decision.get("sufficient"), bool)
        or not isinstance(decision.get("supported_fact_ids"), list)
        or not isinstance(decision.get("unresolved_fact_ids"), list)
        or not isinstance(decision.get("selected_evidence_ids"), list)
    ):
        raise GroundingError("assessment or evidence decision is invalid")

    assessments = {fact.get("fact_id"): fact for fact in assessment["facts"] if isinstance(fact, dict)}
    if set(assessments) != set(fact_ids):
        raise GroundingError("assessment does not match required facts")
    selected_ids = set(decision["selected_evidence_ids"])
    if (
        len(selected_ids) != len(decision["selected_evidence_ids"])
        or not selected_ids.issubset(passage_registry)
        or any(not isinstance(evidence_id, str) for evidence_id in selected_ids)
        or not set(decision["supported_fact_ids"]).issubset(fact_ids)
        or not set(decision["unresolved_fact_ids"]).issubset(fact_ids)
        or set(decision["supported_fact_ids"]) & set(decision["unresolved_fact_ids"])
        or set(decision["supported_fact_ids"]) | set(decision["unresolved_fact_ids"]) != set(fact_ids)
    ):
        raise GroundingError("evidence decision references invalid facts or passages")

    for fact_id, fact in assessments.items():
        evidence_ids = fact.get("evidence_ids")
        if (
            fact.get("status") not in {"supported", "partial", "missing"}
            or not isinstance(evidence_ids, list)
            or any(evidence_id not in selected_ids for evidence_id in evidence_ids)
            or (fact["status"] == "missing" and evidence_ids)
            or (fact["status"] in {"supported", "partial"} and not evidence_ids)
        ):
            raise GroundingError(f"assessment for {fact_id} references invalid evidence")
    if not set(assessment.get("selected_evidence_ids", [])) == selected_ids:
        raise GroundingError("assessment and decision select different evidence")

    return passage_registry
