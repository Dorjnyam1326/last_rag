---
tags: [rag, architecture, migration]
status: design-finalized-implementation-pending
---
# AP - Information needs and facts

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Resolved request-ээс required facts, information needs, dependencies гаргах Planner responsibility.


## Input

- Enhanced Router-ийн **validated** `RouterResult` болон allowed retrieval request-үүд.
- Request тус бүрийн `resolved_question`.
- Шаардлагатай үед зөвшөөрөгдсөн previous-turn context metadata.
- Боломжит capability-ийн canonical жагсаалт: [[Retrieval capabilities]].
- Read-only execution constraints болон request/message budget-ийн context.

Planner өөрөө route/policy-г өөрчлөхгүй; зөвхөн allowed гэж батлагдсан retrieval request-үүдийг төлөвлөнө.

## Processing

1. Request-д хэрэгтэй **information needs**-ийг тодорхойлох.
2. Хариулахад зайлшгүй шаардагдах **required facts**-ийг ялгах.
3. Evidence dependency болон асуултын complexity-г шинжлэх.
4. **Required** болон **preferred** retrieval capabilities санал болгох.
5. Хайх зүйлсийг илэрхийлсэн search intent / information target бэлтгэх.
6. Structured `PlanningProposal` буцаах.

Planner strategy name сонгох шаардлагагүй. Жишээ нь `focused_lookup` required, `multi_angle_coverage` preferred байж болно. Capability-ийн тодорхойлолт ба боломжит mapping-ийг [[Retrieval capabilities]]-ээс авна.

## Information needs ба required facts

Planner answer зохиохгүй; evidence-ээр батлуулах шаардлагуудыг тодорхойлно.

- **Required fact** нь хариултын нэг баталгаажих ёстой мэдээлэл. `F1`, `F2` зэрэг ID нь тухайн request дотор fact-ийг тогтвортой заана.
- **Information need** нь retrieval-д чиглэл өгөх асуулт/зорилго; нэг эсвэл хэд хэдэн required fact-ийг хамарч болно.
- `targets` холбоосоор information need-ийг required fact-уудтай map хийнэ. Ингэснээр query result аль fact-д хамаарахыг дараа шалгаж болно.
- Evidence dependency байвал тэмдэглэнэ; dependency байгаа нь заавал stepwise strategy гэсэн үг биш.
- `multi_part`, `multi_hop`, comparison, exact-reference болон independent `multi_intent` ялгааг дахин ангилахгүй; [[Question taxonomy]]-ийн structure-ийг харгалзаж information need болгон төлөөлнө.

### Жишээ — чөлөөний хоёр шаардлага

Resolved question: `Менежер эзгүй үед 3 цагийн чөлөөг хэн зөвшөөрөх, хүсэлтээ хаана гаргах вэ?`

| Required fact | Information need | Target |
|---|---|---|
| F1: Менежер эзгүй үед хэн зөвшөөрөх вэ? | N1: Орлон зөвшөөрөх эрхийн журам | F1 |
| F2: Хүсэлтийг хаана гаргах вэ? | N2: Чөлөөний хүсэлт бүртгэх журам | F2 |

Энэ бол нэг request-ийн хоёр supported fact. Strategy-г Planner энд шийдэхгүй; Selector capability болон budget-аар шийднэ.

## Хязгаар ба uncertainty

Хэрэглэгчийн асуултанд байхгүй actor, хугацаа, policy нөхцөлийг нэмж fact болгохгүй. Required fact-ийн утга тодорхойгүй бол Router-ийн clarification эсвэл planning limitation boundary руу буцаана; таамгаар query зорилго бүрдүүлэхгүй.

## Navigation

[[Agent Planner]] · [[Enhanced Router]] · [[AP - PlanningProposal contract]]
