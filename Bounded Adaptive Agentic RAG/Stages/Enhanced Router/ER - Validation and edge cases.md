---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# ER - Validation and edge cases

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Structured Router output validation, safe fallback болон routing edge cases.


## Input Validation ба Router Output Validation

Энэ хоёр validation-ийг тусад нь ойлгоно.

| Validation | Байрлал | Хэзээ | Хариуцах зүйл |
|---|---|---|---|
| Input validation | Question and History | LLM-ээс өмнө | Input type/empty/length; safe NFC/whitespace; history schema/role/order; session/turn IDs; context budget |
| Router output validation | Enhanced Router-ийн дараах backend code | LLM-ийн дараа | Structured schema; request count; intent/relationship болон proposed route/policy enum; хоосон `resolved_question`; candidate ID/session/time validity |

Input validation нь **semantic understanding хийхгүй**. Output schema validation нь бүтэц ба зөвшөөрөгдсөн утгыг шалгах боловч `resolved_question` утгын хувьд зөв, meaning хадгалсан гэдгийг батлахгүй. Энэ distinction-ийг normalization regression test-ээр тусад нь баталгаажуулна.

Одоогийн `answer_question()` нь router output-ийн `requests`, request count, intent, relationship, `resolved_question`, `related_turn_ids`-ийг шалгана. `policy`, `route` нь model schema-д орохгүй; backend validated intent-ээс deterministic байдлаар гаргаж, pipeline дахин тооцно. `related_turn_ids` нь зөвхөн Router-д өгсөн recent/candidate ID allowlist-д байх ёстой.

Router output-ийг deterministic Python талд шалгана:

- schema, `route` болон `policy` зөвшөөрөгдсөн утгатай эсэх;
- request count гурван request-ийн limit-д нийцсэн эсэх;
- бүх retrieval request-ийн `resolved_question` хоосон биш эсэх;
- `related_turn_ids` candidate allowlist, зөв session/conversation, current turn-ээс өмнөх нөхцөлийг хангаж байгаа эсэх;
- restricted request-ийн context/result allowed request рүү холилдоогүй эсэх.

Malformed output эсвэл invalid reference дээр буруу context-ийг ашиглахгүй. Intent/referent тодорхойгүй бол clarification; schema/validation унавал router error; retrieval-ийн дараа evidence хүрэхгүй бол insufficient гэж ялгана. **Clarification, insufficient evidence, router error нь өөр төлөвүүд.**

## Жишээ routing

Доорх нь conceptual expected behavior; шинэ runtime test үр дүн биш.

1. `hi` → greeting, direct.
2. `3n tsagiin chuluu yaj avah we?` → workplace/procedure, allowed, retrieval.
3. `tegeed hen zovshooroh ve?` → context хангалттай бол follow-up, standalone `resolved_question`.
4. A/B/C/D/E яриа → E нь candidate A/B-г заавал older-turn follow-up байж болно.
5. `Цалин хэзээ буудаг, миний цалин хэд вэ?` → independent allowed + restricted request; allowed хүсэлт автоматаар цуцлагдахгүй.
6. `Үгүй ээ, 4 биш 3 цагийн чөлөө` → correction subtype, үндсэн relationship нь follow-up байж болно.
7. `Өмнөх хоёр журмын ялгаа юу вэ?` → cross-reference, хэд хэдэн `related_turn_ids`.
8. `Тэр хүсэлтийг хаана гаргах вэ?` → хэд хэдэн боломжит referent байвал clarification.
9. `Ээлжийн амралт хэд хоног вэ?` → бие даасан асуулт бол `new`; сэдэв ижил гэдгээр follow-up болохгүй.

## Implementation status (as built)

- Status: partial. Structured schema constrains intent/relationship/ID types; pipeline validates request list/count, labels, nonblank `resolved_question`, unique allowlisted turn IDs, and relationship-to-ID consistency. Invalid references fail closed to clarification.
- Verified: `tests.test_routing` (7 passed) and 15 focused `AnswerQuestionTests` cover absent history, bounded candidates, original-question guard, ID rejection, selected-turn isolation, restrictions, and valid pipeline flow (2026-11-16; offline mocks).
- Limits: structural validation cannot prove `resolved_question` or selected referent is semantically correct. Allowlist validation does not establish session ownership, creation time, or future-turn authorization; no `session_id` is carried.

## Navigation

[[Enhanced Router]] · [[QH - Scope and contracts]] · [[QH - Edge cases and tests]] · [[ER - Scope and contracts]]
