---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
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

Одоогийн `answer_question()` нь router output-ийн `requests`, request count, intent, relationship, `resolved_question`-ийг шалгадаг. Current runtime output schema-д `policy`, `route`, `related_turn_ids` байхгүй тул тэдгээрийн validation нь proposed design; бүрэн хэрэгжсэн гэж бичихгүй.

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

## Navigation

[[Enhanced Router]] · [[QH - Scope and contracts]] · [[QH - Edge cases and tests]] · [[ER - Scope and contracts]]
