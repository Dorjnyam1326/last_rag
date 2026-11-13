---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# ER - Relationship and references

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Conversation relationship, prior-turn reference сонголт болон follow-up contextualization.


## 2. Conversation relationship ба хуучин turn сонгох

Одоогийн relationship status-ийг хадгална:

- `new`
- `follow_up`
- `clarification`

Conceptual detail-ууд:

- `same_topic`: сэдэв ижил ч өмнөх answer шаардлагагүй бол `new` хэвээр байж болно.
- `correction`: өмнөх асуултын утгыг залруулсан `follow_up`.
- `cross_reference`: хэд хэдэн хуучин turn-ийг холбосон `follow_up`.
- `older_turn`: хамгийн сүүлийн turn биш, өмнөх candidate руу буцсан `follow_up`.
- `ambiguous`: referent тодорхойгүй; шаардлагатай бол `clarification`.

Эдгээрийг бүгдийг тусдаа runtime enum болгохгүй. `relation_detail` болон `related_turn_ids` нь тусдаа metadata хэлбэрийн proposal.

LLM `related_turn_ids`-г зөвхөн `RequestContext`-д өгсөн candidate allowlist-оос сонгоно. Candidate олоогүй turn ID зохиох ёсгүй. **[[Question and history]] candidate-уудыг бэлдэнэ; Enhanced Router semantic сонголт хийнэ; backend ID-г validate хийнэ.**

### A/B/C/D/E жишээ

| Turn | Асуулт |
|---|---|
| A — 101 | 3 цагийн чөлөө яаж авах вэ? |
| B — 102 | Хэн зөвшөөрөх вэ? |
| C — 103 | Ёс зүйн зөрчил гэж юу вэ? |
| D — 104 | Шийдвэрийг давж заалдах уу? |
| E — одоогийн | Менежер эзгүй бол хэн зөвшөөрөх вэ? |

E нь D-тэй биш, B/A-ийн чөлөөний context-тэй холбоотой. [[QH - Context candidates]] candidate discovery-г, [[QH - Conversation links]] relation ба parent-link ойлголтыг дэлгэрүүлнэ.

Backend `related_turn_ids` бүрийг шалгана:

1. ID бодитоор байгаа эсэх.
2. Зөвшөөрөгдсөн ижил conversation/session-д хамаарах эсэх.
3. Одоогийн turn-ээс өмнөх эсэх.
4. Router-т өгсөн candidate allowlist-д байсан эсэх.

Шалгалт амжилтгүй бол тэр reference-д тулгуурласан `resolved_question`-ийг ашиглахгүй; шаардлагатай бол clarification буцаана. Өмнөх assistant answer болон passage нь context болохоос policy truth биш. Retrieval-ийн дараах evidence review эх сурвалжийг дахин шалгана.

## 5. Standalone question resolution

Follow-up үед Router зөвхөн хэрэглэгчийн асуулт болон selected context-оор бие даан ойлгогдох `resolved_question` үүсгэнэ.

- Original: `tegeed manager baihgui bol hen approve hiih ve?`
- Selected context: өмнөх turn-үүд гурван цагийн чөлөөний тухай.
- Resolved: `3 цагийн чөлөө авахад менежер эзгүй бол хэн зөвшөөрөх вэ?`

`resolved_question` Router-ийн output. Required facts, information needs, capabilities-ийг [[Agent Planner]] төлөвлөж `PlanningProposal` гаргана. `single_query`/`multi_query` сонгох болон strategy-specific final query үүсгэх нь [[Select search strategy]]-ийн ажил. Одоогийн schema `search_query` field-тэй тул migration үед compatibility хадгална; field-ийг одоо устгасан гэж үзэхгүй.

> [!note] Хуучин boundary wording
> Өмнөх draft strategy болон final query-г Agent Planner-д оноож байсан. Энэ нь capability-based architecture-тэй зөрчилдсөн тул migration хийхдээ Selector руу шилжүүлсэн; `resolved_question`-ийн Router ownership өөрчлөгдөөгүй.

## Navigation

[[Enhanced Router]] · [[Question and history]] · [[QH - Context candidates]] · [[QH - Conversation links]] · [[ER - Semantic normalization]]
