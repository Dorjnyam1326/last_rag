---
tags: [rag, context, contract]
status: design-finalized-implementation-pending
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
| UI Core-д сүүлийн 6 message өгдөг | Энэ 6 detailed recent message хэвээр |
| Router semantic relation шийддэг | Router-г хэвээр, `related_turn_ids` санал нэмнэ |
| Router prompt semantic normalization хийдэг | Тэр behavior-ийг Router-д хадгалж, нэмэлт LLM call хийхгүй |
| Тусдаа technical input-normalization layer байхгүй | Question and History-д детерминист input/history validation нэмэх (pending design) |
| Follow-up reuse: latest assistant raw passages | Сонгосон linked turn-ийн **reviewed evidence**, provenance |
| Old A/B-ийг шууд сонгож чадахгүй байж болно | older turn summaries + validated IDs |
| Streamlit session state | Эхний prototype-д session state хэвээр, database migration хийхгүй |

## Data contract зарчим

- `original_question` **immutable**; `resolved_question`-ийг Router-оос тусад нь хадгална.
- `messages`-д assistant-ийн чөлөөт текст бол **conversation context**, policy evidence биш.
- Technical input validation ба semantic understanding хоёр тусдаа үе шат; Question and History эхнийхийг, Enhanced Router хоёр дахьг эзэмшинэ.
- `older_candidates` нь ихэвчлэн товч metadata; бүтэн chunk text биш.
- `candidate_ids`-ийг LLM-с ирлээ гээд шууд backend query-д ашиглахгүй, same-session validation хийнэ.
- Link болон evidence нь *нэг хүсэлт (R1/R2/R3)*-д хамаарсан байх ёстой.

## Related

[[Question and history]] · [[QH - RequestContext contract]] · [[QH - Context candidates]] · [[Enhanced Router]]
