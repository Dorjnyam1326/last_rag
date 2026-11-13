---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# SS - Budget and fallback

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Message-level search call budget, multi-intent allocation, fallback болон retry-ийн ялгаа.


## Search budget ба multi-intent allocation

Энд **budget** гэдэг нь Vector Store search operation-ийн тоо. Token, LLM completion, generation length-ийн budget биш.

| Хэмжүүр | V1 initial candidate |
|---|---:|
| User message-ийн shared search cap | 4 call |
| `single_query` | 1 call |
| `multi_query` | 2 call |
| Query бүрийн result limit | Top 5 |

Нэг message дотор олон allowed retrieval request байвал:

- Боломжтой үед request бүрт нэг baseline search эхэлж хуваарилна.
- Үлдсэн budget-ийг preferred multi-query requirement-д зориулсан нэмэлт call-д request-ийн дарааллаар (R1, R2, …) ашиглана; хуваарилалтыг trace-д ил тод үлдээнэ.
- Боломжит corrective retry-д үлдэх budget-ийг shared Controller харгалзана.
- Budget хүрэлцэхгүй request-ийг чимээгүй алгасахгүй; explicit limitation/status гаргана.
- Нийт call hard cap-ийг хэтрүүлэхгүй.

Selector-д remaining budget дамжуулна. **Selector plan-ийн боломжийг урьдчилан шалгана; Controller нийт request execution даяар зарцуулалтыг бүртгэж, hard cap-ийг enforce хийнэ.** Тодорхой allocation tuning нь implementation validation хүлээнэ.

## Fallback ба retry-ийн зааг

V1-ийн allowed fallback: `multi_query → single_query`, зөвхөн:

1. `multi_query` disabled эсвэл 2 call хийх budget хүрэлцэхгүй;
2. `single_query` бүх required capability-г хангах;
3. ганц query executable байх;
4. fallback reason trace-д тэмдэглэгдэх.

Required capability-г өөр enabled strategy хангахгүй бол downgrade хийж биелүүлсэн гэж тэмдэглэхгүй; `planning_limitation` гаргана. Strategy байхгүй үед fallback байхгүй.

Fallback нь **retrieval-ээс өмнөх** Strategy Selector decision. Corrective retry нь **evidence review-ийн дараа** missing evidence-д чиглэсэн өөр component-ийн action ([[Targeted retry within budget]]). Evidence insufficient болон provider execution error нь Selector-ийн strategy fallback нөхцөл биш. Retry/budget ownership-ийг өөрчлөхгүй.

## Navigation

[[Select search strategy]] · [[Targeted retry within budget]] · [[SS - Registry and matching]]
