---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# SS - SearchPlan and validation

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт болон contract-ийг хадгалсан.

## Purpose

Proposal, strategy, query validation, SearchPlan, execution trace болон edge cases.


## Validation

### Proposal input validation

- `PlanningProposal` schema болон `request_id` хүчинтэй.
- `required_facts`, `information_needs` бүтцийн хувьд хүчинтэй, ID-ууд давхардаагүй.
- Information need бүрийн `targets` нь бодит fact ID-уудыг заасан.
- Capability ID-ууд [[Retrieval capabilities]]-д танигдсан.
- `resolved_question` нь query construction-д шаардлагатай үед хоосон биш.

### Strategy validation

- Strategy бүртгэлтэй, enabled.
- Required capability coverage бүрэн.
- Provider болон execution mode дэмжигдсэн.
- Strategy call count үлдсэн shared budget-д багтсан.

### `SearchPlan` validation

- `strategy_id` нь сонгосон бүртгэлтэй strategy.
- Query count strategy limit-ээс хэтрээгүй.
- Query text хоосон биш.
- Safe whitespace normalization-ийн дараа exact duplicate query байхгүй.
- Fact target references хүчинтэй, бүх required fact query-д хамрагдсан.
- Search operation count budget хэтрүүлээгүй.
- Fallback болон requirement limitation зөв тэмдэглэгдсэн.

Query нь тоо, огноо/цаг, үгүйсгэл, албан нэршил, acronym, document reference болон хэрэглэгчийн заасан нөхцөлийг өөрчилж болохгүй. Deterministic schema validation semantic correctness-ийг дангаараа батлахгүй; regression validation шаардлагатай.

## Output — `SearchPlan`

```json
{
  "request_id": "R1",
  "strategy_id": "multi_query",
  "queries": [
    {"id": "Q1", "text": "Богино хугацааны чөлөө зөвшөөрөх эрх, орлон гүйцэтгэх журам", "targets": ["F1"]},
    {"id": "Q2", "text": "3 цагийн чөлөөний хүсэлт гаргах, бүртгүүлэх журам", "targets": ["F2"]}
  ],
  "limit_per_query": 5,
  "fallback_applied": false,
  "fallback_reason": null
}
```

`SearchPlan` нь executable query, strategy ID, target fact, execution limit болон fallback details агуулна. Бодит retrieval result, passage, evidence assessment агуулахгүй.

## Execution trace

Debug болон regression validation-д дараах conceptual талбаруудыг хадгалж болно:

- `request_id`
- `required_capabilities`, `preferred_capabilities`
- `selected_strategy`
- `fallback_applied`, `fallback_reason`
- `estimated_search_calls`, `budget_available`
- `planning_status`

Эдгээр нь trace contract-ийн санал; энэ task-аар database/schema нэмэхгүй.

## Edge cases — validation plan

Доорх нь design validation scenario; unit test хэрэгжсэн эсвэл ажиллуулсан гэсэн үг биш.

| Input | Expected strategy / status | Reason |
|---|---|---|
| Энгийн factual асуулт, `focused_lookup` | `single_query` | Нэг query required capability-г хангана |
| Хоёр өөр мэдээллийн өнцөг | `multi_query` боломжтой бол | Multi-angle coverage хэрэгтэй |
| Required capability registry-д байхгүй | `planning_limitation` | Дэмжигдээгүй чадварыг нуухгүй |
| Preferred capability боломжгүй | Required-ийг хангах strategy; limitation trace | Preferred нь заавал биш |
| `multi_query` disabled | `single_query` зөвхөн required хангаж байвал; үгүй бол limitation | Enabled strategy сонгоно |
| Нэг search slot үлдсэн | `single_query` эсвэл limitation | 2-call plan budget давж болохгүй |
| Search budget 0 | `planning_limitation` | Retrieval call хийх боломжгүй |
| `information_needs` хоосон | Input validation error / planning limitation | Query үндэслэх need байхгүй |
| Давхардсан information need | Proposal validation error эсвэл deduplicate хийсний дараа үргэлжлүүлэх | Давхар төлөвлөгөө үүсгэхгүй |
| Буруу fact target ID | Proposal validation error | Байхгүй fact руу query холбохгүй |
| Query хоосон | `SearchPlan` validation failure | Ашиглах боломжгүй query-г Retrieval-д өгөхгүй |
| Exact duplicate query | `SearchPlan` validation failure | Нэг хайлтыг давтахгүй |
| Strategy metadata буруу | Тухайн strategy-г invalid/disabled гэж үз; боломжгүй бол limitation | Буруу registry metadata-г ажиллуулахгүй |
| Олон allowed request ижил budget өрсөлдөх | Baseline allocation; үлдсэнийг deterministic Controller policy-оор хуваарилах | Нийт cap-ийг барих, чимээгүй алгасахгүй |
| Strategy call count budget-ээс их | Allowed fallback эсвэл limitation | Budget exceed хийхгүй |
| Query rewrite утга өөрчлөх эрсдэлтэй | Meaning хадгалахгүй бол plan validation failure | Тоо, negation, identifier зэргийг хамгаална |

## Navigation

[[Select search strategy]] · [[Retrieval]] · [[SS - Registry and matching]] · [[SS - Budget and fallback]]
