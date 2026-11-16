---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# AP - PlanningProposal contract

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

PlanningProposal-ийн input/output contract, жишээ болон proposed/runtime ялгаа.

## Input contract

Planner нь [[Enhanced Router]]-ийн **validated `RouterResult`**-оос зөвхөн allowed retrieval request-үүдийг авна. Request тус бүрийн `resolved_question`-оос Planner хэрэгтэй input-аа авна; `request_id` Router field биш бөгөөд Python эх `requests[]`-ийн ordinal-аас онооно. Одоогийн implementation `related_turn_ids`-ийг input-д оруулахгүй. Available capability болон read-only execution constraints-ийг Selector-ийн integration-д тусад нь шийднэ.

Intent, relationship, policy-г Planner дахин шийдэхгүй. `related_turn_ids` нь untrusted context metadata тул одоогийн implementation-д Planner input-оос хассан; evidence эсвэл authorization болгон ашиглахгүй. `PlanningProposal` нь нэг independent request-д зориулагдана; олон allowed request бол request тус бүр өөр proposal авна.


## Output — `PlanningProposal`

```json
{
  "request_id": "R1",
  "required_facts": [
    {"id": "F1", "description": "Менежер эзгүй үед хэн зөвшөөрөх вэ?"}
  ],
  "information_needs": [
    {"id": "N1", "focus": "Орлон зөвшөөрөх эрхийн журам", "targets": ["F1"]}
  ],
  "required_capabilities": ["focused_lookup"],
  "preferred_capabilities": ["multi_angle_coverage"]
}
```

Runtime нь энэ logical contract-ийг `rag/planning.py` доторх structured schema болон plain dictionary output-оор гаргана. `request_id`-г Python эх request-ийн дарааллаар өгч, model-оос ID зохиолгохгүй. Энэ нь **хайх шаардлага/зорилгын санал**; executable query болон сонгосон strategy агуулахгүй. [[Select search strategy]] дараагийн phase-д capability ба registry-г шалгаж тусдаа `SearchPlan` гаргана.

## Илүү дэлгэрэнгүй conceptual schema

```json
{
  "request_id": "R1",
  "required_facts": [
    {"id": "F1", "description": "Менежер эзгүй үед чөлөөг хэн зөвшөөрөх вэ?"},
    {"id": "F2", "description": "3 цагийн чөлөөний хүсэлтийг хаана гаргах вэ?"}
  ],
  "information_needs": [
    {"id": "N1", "focus": "Менежер эзгүй үеийн зөвшөөрөх эрх", "targets": ["F1"]},
    {"id": "N2", "focus": "Богино хугацааны чөлөөний хүсэлт бүртгэх журам", "targets": ["F2"]}
  ],
  "required_capabilities": ["focused_lookup"],
  "preferred_capabilities": ["multi_angle_coverage"]
}
```

Энэ JSON нь runtime contract-ийн жишээ. Бодит structured schema болон deterministic validation нь `rag/planning.py`-д байна.

| Field | Зорилго |
|---|---|
| `request_id` | Python-оос эх `requests[]`-ийн ordinal-д тулгуурлан оноосон independent request identity |
| `required_facts[]` | Батлах шаардлагатай fact-уудын `id`, тайлбар |
| `information_needs[]` | Retrieval focus болон холбогдох fact ID (`targets`) |
| `required_capabilities[]` | Хангах зайлшгүй retrieval behavior |
| `preferred_capabilities[]` | Боломжтой үед давуу coverage/behavior |

## Output validation boundary

Selector-д дамжуулахын өмнө deterministic validation дараахыг шалгах загвартай:

- Proposal бүтэц, `request_id`, fact/need ID-ууд хүчинтэй бөгөөд давхардаагүй.
- `information_needs[].targets` нь бодитоор байгаа `required_facts[].id`-уудыг заасан.
- Capability ID-ууд [[Retrieval capabilities]] catalog-д танигдсан.
- Хайх шаардлага хоосон эсвэл утгагүй биш.
- Proposal нь ганц allowed `request_id`-д хамаарсан; өөр request-ийн факт/constraint холилдоогүй.

Энэ validation нь ID, target mapping, capability vocabulary болон request isolation-ийг шалгана; fact-ийн утгын зөв эсвэл retrieval capability үнэхээр ажиллахыг батлахгүй. Selector Registry/provider/budget-ийг тусад нь шалгана.

## Implementation status (as built)

- Status: partial. `plan_requests()` allowed retrieval request бүрд нэг validated dictionary proposal буцаана. Router-аас ирсэн `policy`/`route`-д итгэхгүй; intent-ээс Python-д дахин тооцно.
- Request ID: Python эх `requests[]`-ийн ordinal-аас `R1`–`R3` онооно. Restricted request алгасагдсан ч дараагийн ID pipeline-ийн request numbering-тэй таарна.
- Verification: `tests.test_planning` 6 passed offline. 2026-11-16 live smoke-д нэг structured call хоёр allowed request (`R1`, `R2`)-д proposal буцааж, restricted `R3`-г хассан; шаардлагатай field, target болон capability шалгалт тэнцсэн.
- Limit: Selector байхгүй тул proposal pipeline-д хэрэглэгдэхгүй; `answer_question()` public output-д нэмээгүй. Live smoke нь synthetic бөгөөд fact-ийн эх сурвалжийн үнэн зөвийг шалгаагүй.

## Contract-ийн зааг

`PlanningProposal` нь шаардлага ба capability-ийн **санал**; `SearchPlan` нь Strategy Selector-ийн баталгаажуулсан executable plan. Planner strategy ID, executable query count, `limit_per_query`, fallback эсвэл Retrieval result тогтоохгүй.

## Navigation

[[Agent Planner]] · [[AP - Information needs and facts]] · [[AP - Capability planning]] · [[Select search strategy]]
