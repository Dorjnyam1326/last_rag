---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# SS - Single and Multi Query

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

V1 Single Query ба Multi Query-ийн query construction, fact mapping, execution boundary.


## `single_query` strategy

**Input:** validated `resolved_question`, optional Router `search_query`, information needs, required fact references.

**Processing:** Нэг утгатай search query бэлдэнэ. Router-ийн normalized, request-ID-гаар таарсан `search_query` байвал тэр текстийг өөрчлөлгүй ашиглана; байхгүй/хоосон үед `resolved_question` fallback болно. Required fact target-уудыг query-д холбож, хоосон эсэх болон бүтэц шалгана.

**Output:** яг нэг query агуулсан `SearchPlan`.

Strategy нь API дуудахгүй. V1-д дахин LLM query rewrite хийхгүй. Query construction нь хэрэглэгчийн утга, тоо, нөхцөлийг шинээр нэмэхгүй.

## `multi_query` strategy

**Input:** information needs, required facts, required/preferred capabilities, validated `resolved_question`, optional Router `search_query`.

**Processing:**

1. Өөр өөр evidence need-ийг илэрхийлэх хоёр search intent сонгоно.
2. Intent бүрээс normalized `search_query` (эсвэл blank/missing үед `resolved_question`) дээр тухайн need-ийн focus-ийг залгаж deterministic executable query text бэлдэнэ.
3. Query бүрийг холбогдох required fact ID-тай холбоно.
4. Хоёр query хоосон биш, exact duplicate биш, strategy limit-д багтаж буйг шалгана.
5. Хоёр query болон Top-5 limit-ийг `SearchPlan`-д оруулна.

**Output:** хоёр query төлөвлөсөн `SearchPlan`.

Жишээ нь “Менежер эзгүй үед 3 цагийн чөлөөг хэн зөвшөөрөх, хүсэлтээ хаана гаргах вэ?”:

| Query | Text | Target |
|---|---|---|
| Q1 | Богино хугацааны чөлөө зөвшөөрөх эрх, орлон гүйцэтгэх журам | F1 |
| Q2 | 3 цагийн чөлөөний хүсэлт гаргах, бүртгүүлэх журам | F2 |

Хоёр operation нь өөр information need хайх ёстой. Exact duplicate query гаргахгүй. V1-д нэмэлт LLM semantic validator байхгүй; хоёр query-ийн coverage-ийг regression test-ээр үнэлнэ. Хоёр meaningful query байгуулах боломжгүй бол шаардлагыг шалгаад allowed fallback эсвэл `planning_limitation` хэрэглэнэ.

## Implementation status (as built)

- Status: partial. Pipeline нь Router-ийн nonblank `search_query`-г mapping хийнэ; байхгүй/blank үед validated `resolved_question`-ийг mapping-д өгнө. Selector mapping өгөгдсөн үед хоосон query-г reject хийнэ; standalone дуудлагад mapping-ийг орхивол resolved question query болно. Query бүх required fact ID-г target хийнэ.
- `multi_query` нь яг хоёр ялгаатай information need шаарддаг. Query бүр pipeline-аас өгсөн normalized Router query эсвэл resolved-question fallback-ийг хадгалаад тухайн need-ийн `focus`-ийг залгана; query тус бүр тухайн need-ийн `targets`-ийг авна. Хоёр query үүсгэж чадахгүй үед multi-angle required бол limitation, preferred бол боломжтой single-query fallback гарна.
- `limit_per_query=5` нь одоогийн `search_documents` default болон SSS/Retrieval V1 жишээтэй таарна. Selector provider call хийхгүй.
- Verification: focused offline suite 86 passed; Selector tests normalized query use/fallback validation, pipeline integration test `multi_query`-ийн хоёр initial query болон нэг retry-г shared budget дотор шалгана. C01/C07/C21 integrated live smoke-д `single_query` тус бүр нэг initial operation хийсэн.
- Limit: live multi-query relevance/coverage-г эх баримттай тулгаж үнэлээгүй; full resolved question-ийг хоёр query-д давтах нь query-г урт болгож магадгүй. Retry query нь одоогийн Evidence Reviewer-ээс ирдэг бөгөөд information-need ID-тэй холбогдохгүй.

## Navigation

[[Select search strategy]] · [[Retrieval capabilities]] · [[Retrieval]]
