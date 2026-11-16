---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# ER - Intent and policy

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Request decomposition, semantic intent ба policy/restriction ангиллын зааг.


## 3. Request decomposition

| Бүтэц | Утга |
|---|---|
| `single` | Нэг үндсэн хүсэлт |
| `multi_part` | Нэг сэдвийн олон required fact; заавал салангид request биш |
| `multi_intent` | Бие даасан хүсэлтүүд, request бүр тусдаа policy болон route-той |
| `multi_hop` | Олон нөхцөл/заалтыг холбож хариулах шаардлага |

Нэг сэдвийн “3 цагийн чөлөөг хэн зөвшөөрөх, хэдийд мэдэгдэх вэ?” гэдэг нь `multi_part`; нэг зорилгыг хангах fact-уудаа нэг request дотор хадгалж болно. Харин “Цалин хэзээ буудаг, чөлөө хэд хоног, миний цалин хэд вэ?” гэдэг `multi_intent`; бие даасан хүсэлт бүрийг тусад нь боловсруулах боломжтой. `multi_hop` нь олон agent гэсэн үг биш.

Эхний хувилбар нэг мессежид **дээд тал нь гурван independent request** боловсруулах budget-ийг хадгална. Гурваас олон хүсэлтийг чимээгүй хаяхгүй: бүх хүсэлтэд хариулах боломжгүйг ил тод хэлж, хэрэглэгчээс салгаж илгээхийг хүсэх эсвэл батлагдсан explicit handling хэрэглэнэ.

## 4. Intent ба policy

Request бүрийн semantic intent болон policy decision тусдаа ойлголт. Жишээ:

| Асуулт | Intent | Policy / route |
|---|---|---|
| Сайн уу | greeting | direct |
| Цалин хэзээ буудаг вэ? | payroll | allowed / retrieval |
| Миний цалин хэд вэ? | personal salary | restricted |
| Чөлөө яаж авах вэ? | procedure | allowed / retrieval |
| Python код бичээд өг | general coding | out of scope |

Нэг message-д allowed, restricted, clarification шаардлагатай хүсэлт зэрэгцэж болно. Restricted sub-request нь бусад allowed хүсэлтийг автоматаар хаахгүй.

LLM semantic intent болон санал болгосон route/policy-г гаргаж болно. Backend зөвшөөрөгдсөн route, restriction, tool access болон policy enforcement-ийг хэрэгжүүлнэ. **LLM-ийн classification дангаараа security boundary биш.**

## Implementation status (as built)

- Status: partial. The Router performs semantic intent classification and independent-request decomposition in the existing single structured call; the backend accepts at most three requests.
- Verified: `policy` and `route` are derived in deterministic Python from the validated intent and enabled restriction registry. Pipeline re-derives them before handling each request, so an LLM-suggested route/policy cannot authorize retrieval or bypass a restriction. Offline tests cover the deterministic map, independent allowed/restricted processing, and ignored LLM route/policy suggestions (`tests.test_routing`: 7 passed; focused pipeline phase tests passed, 2026-11-16).
- Not verified: semantic intent accuracy across languages or ambiguous/mixed requests. Deterministic enforcement does not prevent a model from misclassifying an intent; that remains a quality risk.
- Gap: multiple requests are processed within the three-request limit, but per-intent classification quality has not been established against source-reviewed examples. Do not interpret prompt wording as evidence of that quality.

## Navigation

[[Enhanced Router]] · [[Question taxonomy]] · [[Agent Planner]]
