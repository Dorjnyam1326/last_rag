---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# SS - Single and Multi Query

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

V1 Single Query ба Multi Query-ийн query construction, fact mapping, execution boundary.


## `single_query` strategy

**Input:** validated `resolved_question`, information needs, required fact references.

**Processing:** Нэг утгатай search query бэлдэнэ. Default-оор `resolved_question`-ийг query text болгон ашиглаж болно; хэрэгтэй бол information need-ийг өөрчлөлтгүйгээр сонгож ашиглана. Required fact target-уудыг query-д холбож, хоосон эсэх болон бүтэц шалгана.

**Output:** яг нэг query агуулсан `SearchPlan`.

Strategy нь API дуудахгүй. V1-д дахин LLM query rewrite хийхгүй. Query construction нь хэрэглэгчийн утга, тоо, нөхцөлийг шинээр нэмэхгүй.

## `multi_query` strategy

**Input:** information needs, required facts, required/preferred capabilities, validated `resolved_question`.

**Processing:**

1. Өөр өөр evidence need-ийг илэрхийлэх хоёр search intent сонгоно.
2. Intent бүрээс deterministic executable query text бэлдэнэ.
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

## Navigation

[[Select search strategy]] · [[Retrieval capabilities]] · [[Retrieval]]
