---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# ER - Semantic normalization

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Асуултын semantic normalization, context ашиглан утгыг хадгалж resolved question үүсгэх дүрэм.


## Semantic Normalization and Meaning Preservation

Question and History нь input type, хэмжээ, structure, Unicode NFC зэрэг **technical validation + safe text preparation**-ийг хариуцах зорилтот boundary. Энэ нь орчуулга, typo засвар, утгын rewrite хийхгүй. [[Question and history]]-ийн `original_question` яг эхээрээ хадгалагдана; optional `prepared_question` байвал зөвхөн technical-safe processing-ийн үр дүн байна.

Enhanced Router эдгээр input-ийг existing нэг structured LLM call дотор semantic байдлаар ойлгож, relationship/intent/request decomposition-той хамт `resolved_question` үүсгэнэ. Normalization-д зориулж тусдаа LLM call хийхгүй.

| Input type | Original | Expected normalized meaning |
|---|---|---|
| Mongolian Cyrillic | `3 цагийн чөлөө яаж авах вэ?` | `3 цагийн чөлөө яаж авах вэ?` |
| Mongolian Latin | `3n tsagiin chuluu yaj avah ve?` | `3 цагийн чөлөө яаж авах вэ?` |
| English | `Who approves a three-hour leave request?` | `3 цагийн чөлөөний хүсэлтийг хэн зөвшөөрөх вэ?` |
| Mixed language | `3 tsagiin leave avmaar baina, who approves?` | `3 цагийн чөлөө авахыг хэн зөвшөөрөх вэ?` |
| Cyrillic typo | `бйагууллгийн дотоод журамаар члөөг яаж авх вэ?` | `Байгууллагын дотоод журмын дагуу чөлөөг хэрхэн авах вэ?` |
| Latin shorthand | `snu, chuluu yaj avah ve` | `Сайн уу, чөлөө яаж авах вэ?` |
| Follow-up | `tegeed hen approve hiih ve?` | Зөвхөн холбоотой context-оор бүрэн асуулт болгоно |

Эдгээр нь **expected behavior**, production output баталгаа биш.

### Утга хадгалах дүрэм

- Тоо, огноо, цаг, duration, section/document code-ийг яг утгаар нь хадгална (`3 цаг`, `24 цагийн өмнө`, `15:00–18:00`, `10.4.5`).
- `болохгүй`, `авахгүй`, `зөвшөөрөхгүй`, `шаардлагагүй` зэрэг negation болон асуултын polarity-г өөрчлөхгүй.
- `СХНТ`, `DMS`, `CAB`, system/document code, position/organization/person name, exact quote-ийг үндэслэлгүй орчуулах, засахгүй.
- Асуулт эсвэл selected context-д байхгүй `цалинтай`, actor, хугацаа, approval route, policy нөхцөл нэмж болохгүй.
- Утга хэд хэдэн янзаар ойлгогдож, context шийдэхгүй бол дур мэдэн rewrite хийхгүй; clarification гаргана.

Жишээ: `3n tsagiin chuluu avmaar bn` → `3 цагийн чөлөө авмаар байна`. Үүнийг `3 цагийн цалинтай чөлөө` гэж өөрчилж болохгүй.

### `resolved_question` ба хайлтын query

`resolved_question` нь хэрэглэгчийн хүсэлтийн бүрэн, context-тэй асуулт. Одоогийн `classify_intent()` мөн `search_query` гаргадаг; энэ нь compatibility behavior. Зорилтот boundary-д [[Agent Planner]] required facts, information needs болон capabilities бүхий `PlanningProposal` гаргана. [[Select search strategy]] strategy сонгож, strategy-specific final query-уудыг `SearchPlan`-д үүсгэнэ. Эдгээрт шинэ normalization LLM call шаардахгүй.

> [!note] Хуучин boundary wording
> Өмнөх draft final `search_queries[]`-ийг Agent Planner-ийн үүрэг гэж заасан. Энэ attribution нь одоо superseded; Planner capability proposal гаргаж, Selector strategy-specific query-г бэлдэнэ.

Одоогийн `build_search_query()` чөлөөтэй холбоотой query доторх цагийн range-ийг retrieval-ээс хасах дүрэмтэй ч цаг/range заримдаа хайхад чухал байж болно. Энэ бол semantic normalization биш бөгөөд retrieval recall-д нөлөөлөх эсэх нь баталгаажаагүй; одоогийн кодод өөрчлөлт хийхгүй.

## Implementation status (as built)

- Status: partial. `classify_intent()` sends the original question, up to six recent messages, and up to eight older question summaries to the existing single structured Router call; its prompt directs normalization into `search_query` and context-aware `resolved_question` without another LLM call.
- Verified: offline routing tests confirm the original question is unchanged, recent/candidate context is bounded and serialized, and one structured invocation is used (`tests.test_routing`: 7 passed, 2026-11-16).
- Not verified: Cyrillic/Latin/English meaning preservation, typo correction, negation/number fidelity, and source-grounded semantic quality. Prompt text and mocked output do not establish those outcomes.
- Dependency/gap: older reference resolution is now technically supported only within the bounded catalogue; semantic accuracy remains unverified, and references beyond the latest eight older candidates cannot be resolved from this input.

## Navigation

[[Enhanced Router]] · [[Question and history]] · [[ER - Relationship and references]]
