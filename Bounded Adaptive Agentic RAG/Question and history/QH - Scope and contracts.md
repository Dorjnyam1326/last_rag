---
tags: [rag, context, contract]
status: design-finalized-implementation-partial
---
# QH - Scope and contracts

## Purpose

`Question and history` нь **context бэлтгэгч**, semantic шийдвэр гаргагч биш. Одоогийн user question, recent messages, older candidate metadata-г нэг бүтэцтэй багц болгож [[Enhanced Router]]-т дамжуулна.

## Хариуцлага

**Хийнэ:**

- **Target:** input type, empty/whitespace-only, length limit-ийг deterministic Python-оор шалгах.
- **Target:** safe Unicode NFC болон хязгаартай whitespace normalization; exact text-sensitive хэсгийг эвдэхгүй.
- `original_question`-ийг бүх хэлний холимог, typo-той нь өөрчлөлтгүй хадгалах.
- Шаардлагатай бол technical preparation-ийн үр дүнг optional `prepared_question`-д хадгалах; энэ нь semantic rewrite биш.
- `session_id`, `message_id`, `turn_id`-ийг тогтвортой холбох.
- **Target:** history-ийн бүтэц, role, chronological order болон identifier-уудыг шалгаж авах.
- Сүүлийн `MAX_HISTORY_MESSAGES=6`-г detailed recent context болгон сонгох.
- Older turn-уудын товч metadata candidate-уудыг ялгаж, хэмжээ/token-ийг хязгаарлах.
- Сонгосон ID-ууд session-д харьяалагдаж байгаа эсэхийг кодоор шалгах.
- Context size/token budget-ийг барих.

**Хийхгүй:**

- Mongolian Latin-ийг Cyrillic болгох, English орчуулах, Cyrillic typo засах.
- `follow_up/new/clarification` ангилах; ямар previous turn-д хамаарахыг тогтоох.
- Latin→Cyrillic rewrite, intent/restriction classification, `search_query` гаргах.
- Vector Store search, evidence sufficiency, retry, generation.
- Retrieval strategy сонгох.

## Current vs target

| Өнөөдрийн `refactor` | Зорилтот design |
|---|---|
| UI бүх session history-г өгнө; QH Router-д сүүлийн 6 detailed message, түүнээс өмнөх хамгийн ихдээ 8 user-question summary бэлтгэнэ | Энэ 6 detailed recent message болон bounded candidate catalogue хэвээр |
| Router нэг structured call-аар semantic relation, `resolved_question`, per-request `related_turn_ids` гаргана | Router-г хэвээр; `relation_detail` болон чанарын баталгаажуулалтыг үргэлжлүүлнэ |
| Router prompt semantic normalization хийдэг | Тэр behavior-ийг Router-д хадгалж, нэмэлт LLM call хийхгүй |
| `answer_question()` non-string болон хоосон асуултыг Router-оос өмнө шалгаж, original text-ийг хэвээр дамжуулна; QH 6 detailed message + 8 хүртэл older question summary өгнө | Length limit, history schema/session identity validation, safe NFC/whitespace preparation, candidate quality-г хэмжих |
| Follow-up reuse: Router-ийн сонгосон turn-ийн raw passages-ийг reviewer дахин шалгана | Сонгосон linked turn-ийн request-scoped **reviewed evidence**, provenance |
| Candidate каталог нь recent window-ээс гаднах хамгийн сүүлийн 8 user question-ийг chronological байдлаар оруулна | Semantic candidate ranking, validated session identity ба time authorization |
| Streamlit session state | Эхний prototype-д session state хэвээр, database migration хийхгүй |

## Implementation status (as built)

- Status: partial. `answer_question()` input type/empty guard-ийг Router-оос өмнө хийж original question-ийг хэвээр дамжуулна. UI user/assistant pair-д UUID `turn_id` хадгалж, full session history-г pipeline-д өгнө.
- Verified: QH зургаан recent message болон хамгийн ихдээ найман chronological older user-question summary (summary бүр 240 тэмдэгт хүртэл) бэлдэнэ. Router-ийн per-request `related_turn_ids`-ийг тухайн history input-ын allowlist-аар шалгана; pipeline зөвхөн сонгосон turn-ийн raw passages-ийг reviewer-т дахин өгнө.
- Limits: `RequestContext` class, `session_id`, max-length guard, NFC, semantic candidate ranking, request-level prior-evidence isolation байхгүй. Өгсөн history allowlist нь user/session authorization биш.
- Verification: `tests.test_routing` 7, focused pipeline 15, UI history 1 тест passed offline; өргөн pipeline class 33 тестээс 11 assertion/fixture failure-тэй.

## Data contract зарчим

- `original_question` **immutable**; `resolved_question`-ийг Router-оос тусад нь хадгална.
- `messages`-д assistant-ийн чөлөөт текст бол **conversation context**, policy evidence биш.
- Technical input validation ба semantic understanding хоёр тусдаа үе шат; Question and History эхнийхийг, Enhanced Router хоёр дахьг эзэмшинэ.
- `older_candidates` нь ихэвчлэн товч metadata; бүтэн chunk text биш.
- `related_turn_ids`-ийг LLM-с ирлээ гээд шууд backend query-д ашиглахгүй; supplied history allowlist-аар шалгана. Энэ нь same-session ownership validation биш.
- Link болон evidence нь *нэг хүсэлт (R1/R2/R3)*-д хамаарсан байх ёстой.

## Related

[[Question and history]] · [[QH - RequestContext contract]] · [[QH - Context candidates]] · [[Enhanced Router]]
