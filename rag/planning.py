import json
import os
import re
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from rag.routing import (
    INTENT_LABELS,
    MAX_ROUTED_REQUESTS,
    RELATIONSHIP_LABELS,
    get_policy_and_route,
)


CAPABILITY_DESCRIPTIONS = {
    "focused_lookup": "Нэг тодорхой information need-д төвлөрөх.",
    "multi_angle_coverage": "Өөр өөр evidence angle шаардлагатай үед хамруулах.",
    "exact_reference_focus": "Тодорхой заалт, дугаар эсвэл identifier-д төвлөрөх.",
    "comparison_coverage": "Харьцуулах тал бүрийн evidence шаардлагыг гаргах.",
    "dependency_aware_search": "Эхний evidence-ээс хамаарах дараагийн мэдээллийн хэрэгцээ.",
    "metadata_scoping": "Document/version зэрэг metadata-аар хайлтыг хязгаарлах хэрэгцээ.",
}
CAPABILITY_IDS = tuple(CAPABILITY_DESCRIPTIONS)


class _RequiredFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Request доторх F1, F2 гэх мэт fact ID")
    description: str = Field(description="Evidence-ээр батлах нэг fact")


class _InformationNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Request доторх N1, N2 гэх мэт need ID")
    focus: str = Field(description="Retrieval-аар тодруулах information target")
    targets: list[str] = Field(
        min_length=1,
        description="Энэ need-ийн дэмжих required fact ID-ууд",
    )


class _PlanningProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(description="Input-д Python-оос өгсөн request ID-г яг хуул")
    required_facts: list[_RequiredFact] = Field(
        min_length=1,
        description="Хариулахад evidence-ээр батлах зайлшгүй fact-ууд",
    )
    information_needs: list[_InformationNeed] = Field(
        min_length=1,
        description="Required fact-уудыг хайхад хэрэгтэй retrieval focus-ууд",
    )
    required_capabilities: list[Literal[*CAPABILITY_IDS]] = Field(
        min_length=1,
        description="Биелүүлэхэд зайлшгүй шаардлагатай capability ID-ууд",
    )
    preferred_capabilities: list[Literal[*CAPABILITY_IDS]] = Field(
        description="Боломжтой үед илүүд үзэх capability ID-ууд",
    )


class _PlanningProposals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[_PlanningProposal] = Field(
        description="Allowed retrieval request бүрд яг нэг proposal; input дарааллыг хадгал",
    )


_FACT_ID = re.compile(r"F[1-9][0-9]*\Z")
_NEED_ID = re.compile(r"N[1-9][0-9]*\Z")


def _allowed_requests(requests: list[dict]) -> list[dict]:
    if not isinstance(requests, list) or not requests or len(requests) > MAX_ROUTED_REQUESTS:
        raise ValueError("router requests must be a non-empty list of at most three items")

    allowed = []
    for index, request in enumerate(requests, start=1):
        if not isinstance(request, dict):
            raise ValueError("router request must be an object")
        intent = request.get("intent")
        relationship = request.get("relationship")
        if intent not in INTENT_LABELS or relationship not in RELATIONSHIP_LABELS:
            raise ValueError("router request has invalid intent or relationship")

        if get_policy_and_route(intent)["route"] != "retrieval":
            continue
        question = request.get("resolved_question")
        if relationship == "clarification":
            continue
        if not isinstance(question, str) or not question.strip():
            raise ValueError("allowed router request needs a resolved question")

        # Match the pipeline's R1/R2/R3 numbering, including skipped requests.
        allowed.append(
            {
                "request_id": f"R{index}",
                "intent": intent,
                "resolved_question": question,
            }
        )
    return allowed


def _as_dict(value) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    raise ValueError("planning proposal must be an object")


def _validate_proposals(proposals, allowed_requests: list[dict]) -> list[dict]:
    if not isinstance(proposals, list) or len(proposals) != len(allowed_requests):
        raise ValueError("planner must return exactly one proposal per allowed request")

    expected_ids = [request["request_id"] for request in allowed_requests]
    normalized = {}
    for item in proposals:
        proposal = _as_dict(item)
        if set(proposal) != {
            "request_id",
            "required_facts",
            "information_needs",
            "required_capabilities",
            "preferred_capabilities",
        }:
            raise ValueError("proposal has fields outside the planning contract")
        request_id = proposal.get("request_id")
        if request_id not in expected_ids or request_id in normalized:
            raise ValueError("planner returned a missing, duplicate, or unknown request ID")

        facts = proposal.get("required_facts")
        needs = proposal.get("information_needs")
        required = proposal.get("required_capabilities")
        preferred = proposal.get("preferred_capabilities")
        if not isinstance(facts, list) or not facts:
            raise ValueError("proposal must contain required facts")
        if not isinstance(needs, list) or not needs:
            raise ValueError("proposal must contain information needs")
        if not isinstance(required, list) or not required:
            raise ValueError("proposal must contain required capabilities")
        if not isinstance(preferred, list):
            raise ValueError("preferred capabilities must be a list")

        fact_ids = []
        clean_facts = []
        for raw_fact in facts:
            fact = _as_dict(raw_fact)
            if set(fact) != {"id", "description"}:
                raise ValueError("required fact has fields outside the planning contract")
            fact_id = fact.get("id")
            description = fact.get("description")
            if (
                not isinstance(fact_id, str)
                or not _FACT_ID.fullmatch(fact_id)
                or fact_id in fact_ids
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise ValueError("proposal has an invalid or duplicate fact")
            fact_ids.append(fact_id)
            clean_facts.append({"id": fact_id, "description": description})

        need_ids = []
        clean_needs = []
        for raw_need in needs:
            need = _as_dict(raw_need)
            if set(need) != {"id", "focus", "targets"}:
                raise ValueError("information need has fields outside the planning contract")
            need_id = need.get("id")
            focus = need.get("focus")
            targets = need.get("targets")
            if (
                not isinstance(need_id, str)
                or not _NEED_ID.fullmatch(need_id)
                or need_id in need_ids
                or not isinstance(focus, str)
                or not focus.strip()
                or not isinstance(targets, list)
                or not targets
                or any(not isinstance(target, str) for target in targets)
                or len(set(targets)) != len(targets)
                or any(target not in fact_ids for target in targets)
            ):
                raise ValueError("proposal has an invalid information need or fact target")
            need_ids.append(need_id)
            clean_needs.append({"id": need_id, "focus": focus, "targets": targets})

        for capability_list in (required, preferred):
            if any(
                not isinstance(capability, str) or capability not in CAPABILITY_IDS
                for capability in capability_list
            ) or len(set(capability_list)) != len(capability_list):
                raise ValueError("proposal has an invalid or duplicate capability")
        if set(required) & set(preferred):
            raise ValueError("a capability cannot be both required and preferred")

        normalized[request_id] = {
            "request_id": request_id,
            "required_facts": clean_facts,
            "information_needs": clean_needs,
            "required_capabilities": required,
            "preferred_capabilities": preferred,
        }

    if set(normalized) != set(expected_ids):
        raise ValueError("planner omitted an allowed request")
    return [normalized[request_id] for request_id in expected_ids]


def plan_requests(
    requests: list[dict],
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Return one validated planning proposal per allowed retrieval request."""
    allowed_requests = _allowed_requests(requests)
    if not allowed_requests:
        return []

    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    from langchain_openai import ChatOpenAI

    capability_catalog = [
        {"id": capability_id, "description": description}
        for capability_id, description in CAPABILITY_DESCRIPTIONS.items()
    ]
    system_prompt = (
        "Allowed workplace retrieval requests бүрийн evidence planning proposal гарга. "
        "Оролт дахь request_id-г яг хуул; шинэ ID зохиохгүй. Request бүрийн гол "
        "асуултаас evidence-ээр батлах required_facts болон тэдгээрт хүрэх "
        "information_needs гаргаж, need бүрийн targets-д зөвхөн тухайн request-ийн "
        "fact ID ашигла. Утгагүй шаардлага нэмж болохгүй; текстэд байхгүй actor, "
        "хугацаа, policy нөхцөл бүү таамагла. Capability-г доорх catalog-аас л сонго; "
        "эдгээр нь санал болгож буй retrieval behavior болохоос runtime-д боломжтой "
        "strategy байгаа гэсэн баталгаа биш. Planner strategy, executable query, "
        "budget эсвэл answer гаргахгүй. Request-үүдийн facts/needs-ийг хооронд нь "
        "хольж болохгүй. Нэг structured response-д input дарааллаар нэг proposal "
        "бүр буцаа. related_turn_ids болон policy/route энэ planning input-д ороогүй; "
        "retrieval authorization шийдэхгүй."
    )
    human_input = json.dumps(
        {"requests": allowed_requests, "capability_catalog": capability_catalog},
        ensure_ascii=False,
    )
    response = (
        ChatOpenAI(
            model=model or os.getenv("OPENAI_MODEL", "gpt-6-luna"),
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            max_completion_tokens=1600,
            timeout=45,
            max_retries=0,
            service_tier="default",
            stream_usage=True,
        )
        .with_structured_output(_PlanningProposals, method="json_schema")
        .invoke([("system", system_prompt), ("human", human_input)])
    )
    return _validate_proposals(response.proposals, allowed_requests)
