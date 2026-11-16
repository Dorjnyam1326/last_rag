---
tags: [rag, decisions, context]
status: accepted-design
---
# QH - Design decisions

## Баталсан шийдвэрүүд

| ID | Шийдвэр | Яагаад |
|---|---|---|
| QH-01 | Original question хэвээр хадгалах | Semantic rewrite-ийг зөв шатанд хийх |
| QH-02 | `MAX_HISTORY_MESSAGES=6` хэвээр | Одоогийн context budget-ийг хадгалах |
| QH-03 | Recent detailed + older metadata-г ялгах | A/B/C/D/E хуучин reference дэмжих |
| QH-04 | `topic_id` ба `related_turn_ids` өөр ойлголт | Ижил сэдэв ≠ direct follow-up |
| QH-05 | Reference resolution Router-д байна | Question + History-ийг жижиг, ойлгомжтой байлгах |
| QH-06 | Chosen turn ID-г backend validate хийнэ | Cross-session болон invalid ID хамгаалах |
| QH-07 | Reused evidence-ийг дахин review хийнэ | Old answer/document truth биш |
| QH-08 | Multi-intent relation per request | R1/R2/R3 холилдохгүй |
| QH-09 | Refactor эхлээд session state | Persistent DB migration одоохондоо хэрэггүй |
| QH-10 | RAG Admin өөрчлөхгүй | Төслийн scope-ийг хадгалах |
| QH-11 | Question taxonomy дөрвөн independent dimension-тэй | Relationship, intent/policy, structure, retrieval strategy-г нэг enum-д хутгахгүй |
| QH-12 | Question and History semantic router биш | Зөвхөн input context/candidate бэлтгэнэ |
| QH-13 | Existing router-ийг хадгалж enhance хийнэ | `classify_intent()`-ийг бүрэн солихгүй |
| QH-14 | `related_turn_ids` нь Router-ийн per-request output | Supplied history allowlist-д Python validate хийнэ; session ownership-ийн баталгаа биш |
| QH-15 | `topic_id` нь follow-up-ийн баталгаа биш | Ижил topic ба холбоотой turn өөр ойлголт |
| QH-16 | `MAX_HISTORY_MESSAGES=6` эхний context limit хэвээр | UI-н бүх session history-г хадгалахтай зөрчилдөхгүй |
| QH-17 | Older linked context нь recent window-ээс тусдаа candidate concept | 6 message-ээс хуучин context-ийг сонгож, validate хийсний дараа ачаална |
| QH-18 | Agent Planner зөвхөн retrieval strategy шийднэ | Router relationship/policy/structure-г дахин шийдэхгүй |
| QH-19 | Conceptual relation detail-ууд runtime enum биш | Эхлээд `new/follow_up/clarification`-ийг хадгална |
| QH-20 | Technical ба semantic normalization тусдаа responsibility | Input preparation ба утгын шийдвэрийг ялгана |
| QH-21 | Technical validation нь deterministic Python responsibility | Empty/type/size/history/context checks-ийг LLM-д даатгахгүй |
| QH-22 | Semantic normalization Enhanced Router-ийн existing structured LLM call-д байна | Нэмэлт standalone normalization call, latency үүсгэхгүй |
| QH-23 | `original_question` өөрчлөлтгүй хадгалагдана | Audit/debug болон эх утгыг хамгаална |
| QH-24 | Safe Unicode normalization нь NFC; whitespace зөвхөн аюулгүй хүрээнд | Code, quote, identifier зэрэг exact text-sensitive утгыг эвдэхгүй |
| QH-25 | Mongolian Cyrillic нь normalized natural-language target | Latin, English, mixed input-ийн утгыг ойлгомжтой илэрхийлнэ; acronym/code-г хүчээр хөрвүүлэхгүй |
| QH-26 | Тоо, хугацаа, үгүйсгэл, албан нэр/identifier хадгалагдана | Утгын өөрчлөлт, policy hallucination-оос сэргийлнэ |
| QH-27 | Semantic ambiguity-г дур мэдэн rewrite хийхгүй | Context хүрэхгүй бол clarification шаардлагатай |
| QH-28 | Output schema validation semantic correctness-ийг батлахгүй | Schema ба утгын зөв байдлыг тусад нь үнэлнэ |
| QH-29 | Normalization quality-г regression test-ээр баталгаажуулна | Prompt instruction дангаараа чанарын нотолгоо биш |

## Дараа шийдэх тохиргоо

- Older candidate-ийн count 8, summary cap 240 characters нь эхний implementation setting; coverage, semantic чанар, нийт token budget-ийг цааш хэмжиж шалгах.
- `topic_id` үүсгэх/өөрчлөх schema (Router-ийн output-той хамт).
- Link confidence ба ambiguous үед clarification хийх бодлого.
- Handoff-ийн exact Python `TypedDict` / Pydantic schema (implementation үед).
- Истори persistence шаардлагатай бол яаж хадгалах (энэ шатанд хийхгүй).

## What is done / what is next

**Done (design):** Context scope, limit, candidates, relation references, evidence separation, Router handoff, [[Question taxonomy]]-ийн дөрвөн хэмжээсийн boundary.

**Current implementation:** UI full session history болон user/assistant pair-ийн UUID turn ID хадгална. QH зургаан recent message ба найман хүртэлх older question summary бэлдэнэ. `classify_intent()` нэг structured call-аар `new/follow_up/clarification`, intent, `search_query`, `resolved_question`, per-request `related_turn_ids` гаргаж, Python ID-г input allowlist-аар шалгана. `topic_id`, semantic candidate ranking болон Agent Planner implementation байхгүй.

`classify_intent()`-ийн prompt Latin Mongolian, English, mixed language, typo-г утгаар нь тайлбарлахыг чиглүүлдэг. `answer_question()` одоо non-string, empty, whitespace-only оролтыг Router-оос өмнө шалгана. Гэхдээ maximum-length check, Unicode NFC/text preparation болон normalization quality regression хараахан баталгаажаагүй. `answer_question()` зарим router output field болон request count-ийг шалгадаг ч энэ нь semantic зөв байдлыг батлахгүй.

**Next:** Session ownership болон request-scoped prior evidence/provenance-ийн бодит contract-ийг шийдэх; normalization ба referent accuracy-г source-verified evaluation-аар шалгах. Үүний дараа [[Agent Planner]] phase-г үргэлжлүүлнэ.

## Related

[[Question taxonomy]] · [[Question and history]] · [[QH - Edge cases and tests]]
