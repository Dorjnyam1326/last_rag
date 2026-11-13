---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
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

## Navigation

[[Enhanced Router]] · [[Question taxonomy]] · [[Agent Planner]]
