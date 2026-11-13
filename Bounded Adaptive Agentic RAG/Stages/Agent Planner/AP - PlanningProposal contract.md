---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# AP - PlanningProposal contract

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

PlanningProposal-ийн input/output contract, жишээ болон proposed/runtime ялгаа.

## Input contract

Planner нь [[Enhanced Router]]-ийн **validated `RouterResult`**-оос зөвхөн allowed retrieval request-үүдийг авна. Request тус бүрт `request_id`, `resolved_question`, шаардлагатай үед validated related-context metadata байна. Available capability болон read-only execution constraints-ийг хамт өгч болно.

Intent, relationship, policy-г Planner дахин шийдэхгүй. `PlanningProposal` нь нэг independent request-д зориулагдана; олон allowed request бол request тус бүр өөр proposal авна.


## Output — `PlanningProposal`

```json
{
  "request_id": "R1",
  "required_facts": ["Менежер эзгүй үед хэн зөвшөөрөх вэ?"],
  "information_needs": ["Орлон зөвшөөрөх эрхийн журам"],
  "required_capabilities": ["focused_lookup"],
  "preferred_capabilities": ["multi_angle_coverage"]
}
```

Энэ бол proposed logical contract, runtime schema биш. Энэ нь **хайх шаардлага/зорилгын санал**; executable query-ууд болон сонгосон strategy агуулсангүй. [[Select search strategy]] capability ба registry-г шалгасны дараа тусдаа `SearchPlan` гаргана.

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

Энэ JSON нь [[Retrieval capabilities]] дэх conceptual example-тэй ижил contract; бодит runtime schema биш.

| Field | Зорилго |
|---|---|
| `request_id` | Router-оос ирсэн independent request-ийн identity |
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

Энэ schema validation нь fact-ийн утгын зөв эсвэл retrieval capability үнэхээр ажиллахыг батлахгүй. Selector Registry/provider/budget-ийг тусад нь шалгана. Энэ нь proposed validation contract; implementation болон regression tests pending.

## Contract-ийн зааг

`PlanningProposal` нь шаардлага ба capability-ийн **санал**; `SearchPlan` нь Strategy Selector-ийн баталгаажуулсан executable plan. Planner strategy ID, executable query count, `limit_per_query`, fallback эсвэл Retrieval result тогтоохгүй.

## Navigation

[[Agent Planner]] · [[AP - Information needs and facts]] · [[AP - Capability planning]] · [[Select search strategy]]
