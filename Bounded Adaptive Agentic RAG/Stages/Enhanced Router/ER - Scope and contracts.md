---
tags: [rag, architecture, migration]
status: design-finalized-implementation-partial
---
# ER - Scope and contracts

> [!note] Migration
> Доорх хэсгүүдийг өмнөх component hub-ээс бүрэн шилжүүлэв. Source section-ийн тайлбар, жишээ, хүснэгт, diagram болон status-ийг хадгалсан.

## Purpose

Enhanced Router-ийн input/output contract, structured call, component boundary болон implementation төлөв.


## Зорилго ба хариуцлагын зааг

Enhanced Router нь [[Question and history]]-ээс ирсэн `RequestContext`-ийг ойлгож, [[Agent Planner]]-т өгөх `RouterResult` гаргана. [[Question taxonomy]]-ийн A/B/C хэмжээсийг шийднэ:

1. **Question understanding:** original асуултын утга, хэл, нөхцөлийг хадгална.
2. **Conversation relationship:** шинэ асуулт уу, follow-up уу, clarification хэрэгтэй юу гэдгийг шийдэж, proposal загварт холбогдох turn-ийг сонгоно.
3. **Request decomposition:** нэг message доторх бие даасан хүсэлтүүдийг request болгон ялгана.
4. **Intent and policy classification:** хүсэлт бүрийн зорилго болон санал болгож буй policy/route-ийг тусад нь гаргана.
5. **Standalone question resolution:** follow-up-д зөвхөн хүчинтэй context-ийг ашиглан бие даан ойлгогдох асуулт үүсгэнэ.

Router **retrieval strategy сонгохгүй**, Vector Store хайлт хийхгүй, evidence review эсвэл final answer generation хийхгүй. [[Agent Planner]] нь information needs болон capability санал болгож `PlanningProposal` гаргана; strategy implementation болон executable query plan-ийг [[Select search strategy]] сонгоно.

> [!warning] Migration үеийн зөрчил
> Өмнөх hub-д “Retrieval strategy болон хайлтын query төлөвлөлт нь Agent Planner-ийн boundary” гэж бичиж, хүснэгтэд “Single/multi-query strategy сонгох” болон “Search query төлөвлөх” ажлыг Planner-д өгсөн байв. Энэ нь эцэслэсэн capability-based design-тай зөрчилдөнө. Одоогийн boundary: `Agent Planner → capability proposal`, `Select Search Strategy → strategy/query plan`. Хуучин attribution-г migration history болгон энд тэмдэглэж, current contract-ийг шинэчиллээ.

## Input → Processing → Output

| | Design contract |
|---|---|
| **Input** | Original question, turn ID-тай хамгийн ихдээ 6 recent user/assistant message, 8 хүртэлх older user-question summary. UI full history-г өгч, QH үүнийг bounded болгоно |
| **Processing** | Нэг structured LLM output дотор semantic ойлголт, relationship/reference resolution, independent request ялгалт, intent/policy suggestion, resolved question |
| **Backend validation** | Structured schema, request count, intent/relationship, `resolved_question`, `related_turn_ids` input allowlist; policy/route-ийг Python validated intent-ээс deterministic байдлаар гаргана. Session ownership/time validation байхгүй |
| **Output** | Одоогийн `requests[]`: request бүрийн `intent`, `search_query`, `relationship`, `resolved_question`, `related_turn_ids`, Python-derived `policy`/`route`. Тусдаа `RouterResult` class байхгүй |

Эдгээр таван ойлголтыг тус тусын шат шиг дүрсэлж болох ч **таван тусдаа LLM call гэсэн үг биш**. Эхний загварт нэг `classify_intent()` structured call бүх semantic ангиллыг гаргаж болно; Python тал хатуу нөхцөлийг validate/enforce хийнэ.

```mermaid
flowchart TD
    A["RequestContext"] --> B["Enhanced Router LLM"]
    B --> C["Understand question and history"]
    C --> D["Resolve relationship and references"]
    D --> E["Identify independent requests"]
    E --> F["Classify intent and policy"]
    F --> G["Create resolved questions"]
    G --> H["Backend schema and ID validation"]
    H --> I{"Valid result?"}
    I -->|No| J["Safe fallback or clarification"]
    I -->|Yes| K["Process each request"]
    K --> L{"Route"}
    L -->|Direct| M["Direct response"]
    L -->|Restricted| N["Policy response"]
    L -->|Clarification| O["Ask user"]
    L -->|Retrieval| P["Agent Planner"]
    class P internal-link;
```

## RouterResult contract

Доорх JSON нь **proposed contract**, одоогийн implementation-ийн яг output биш:

```json
{
  "relationship": "follow_up",
  "relation_detail": "older_turn",
  "related_turn_ids": [101, 102],
  "needs_clarification": false,
  "requests": [
    {
      "id": "R1",
      "relationship": "follow_up",
      "related_turn_ids": [101, 102],
      "resolved_question": "3 цагийн чөлөө авахад менежер эзгүй бол хэн орлон зөвшөөрөх вэ?",
      "intent": "approval",
      "policy": "allowed",
      "route": "retrieval"
    }
  ]
}
```

`multi_intent` үед request бүр өөрийн `relationship`, `related_turn_ids`, `intent`, `policy`, `route` агуулж болно. Top-level `relationship` нь compatibility metadata байж болохоос mixed request-үүдийг нэг глобал утгаар шийдэхгүй. `topic_id` шаардлагатай бол backend existing topic-той холбох эсвэл authoritative ID үүсгэнэ; LLM-ийн дурын ID-г шууд итгэмжлэхгүй.

## Router ба Agent Planner-ийн зааг

| Үүрэг | Enhanced Router | Agent Planner |
|---|---|---|
| Original question ойлгох | Тийм | Үгүй |
| Conversation relationship болон follow-up resolve хийх | Тийм | Үгүй |
| Холбогдох turn сонгох | Тийм | Үгүй |
| Intent/policy ангилах | Тийм | Үгүй |
| Independent request салгах | Тийм | Үгүй |
| Standalone question гаргах | Тийм | Үгүй |
| Required facts тодорхойлох | Үгүй | Тийм |
| Strategy implementation сонгох | Үгүй | Үгүй; Selector хариуцна |
| Required facts/capabilities төлөвлөх | Үгүй | Тийм |
| Executable search query төлөвлөх | Үгүй | Үгүй; Selector strategy-аас хамааруулна |
| Retry strategy | Үгүй | Controller-той хамт |
| Evidence review | Үгүй | Үгүй |

Planner Router-ийн semantic classification-ийг дахин эхнээс нь хийхгүй. Router нь Vector Store search, evidence grading, final generation хийхгүй.

## Implementation status (as built)

| Одоогийн `refactor` | Pending design/dependency |
|---|---|
| `classify_intent()` нэг structured call-аар relationship, intent, decomposition, query болон per-request `related_turn_ids` гаргана | Бүрэн `RequestContext` болон өргөтгөсөн `RouterResult` class |
| Relationship: `new / follow_up / clarification`; usable history/valid ID байхгүй follow-up clarification болно | `relation_detail`, session ownership/time authorization |
| Policy/route нь validated intent + restriction registry-гээс Python-д гарна; pipeline тэдгээрийг дахин тооцож enforcement хийнэ. ID supplied-history allowlist-аар шалгана | Candidate search/ranking ба session identity-тэй validation |
| Нэг мессежид 3 хүртэл агуулгын request; холимог мессежийн нэг greeting-ийг эхний preamble болгон тусгаарлана. 3-аас олон агуулгын request бол safe clarification | Capability plan-ийг [[Agent Planner]], strategy болон executable query-г [[Select search strategy]] хариуцах boundary |

- Status: partial
- Evidence: `tests.test_routing` (7 passed), 15 focused `AnswerQuestionTests` passed, offline mocks, 2026-11-16. Broader pipeline class: 33 run, 11 stale assertion/fixture failures.
- Latest verification: offline greeting + three substantive requests regression and full test suite (132 passed), 2026-11-16.
- Limits: semantic normalization/intent/reference accuracy source-verified label эсвэл live run-аар батлагдаагүй. Supplied-history allowlist нь cross-session identity батлахгүй; raw previous passages request-scoped provenance-гүй.
- Deviation: одоогийн runtime `policy`/`route`-ийг LLM output-оос авахгүй; validated intent-ээс Python гаргаж байна. LLM route/policy санал security boundary биш.

## Navigation

[[Enhanced Router]] · [[Question and history]] · [[Agent Planner]] · [[ER - Validation and edge cases]]
