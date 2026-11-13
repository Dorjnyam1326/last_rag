---
tags: [rag, agentic-rag, router]
status: design-finalized-implementation-pending
---
# Enhanced Router

> [!note] Төлөв
> Architecture design дууссан; Python implementation болон runtime validation pending. Доорх subnote-ууд хуучин hub-ийн дэлгэрэнгүй агуулгыг хадгална.

## Purpose

Enhanced Router нь [[Question and history]]-ээс бэлтгэсэн bounded context-ийг ойлгож, хэрэглэгчийн request-үүдийн relationship, intent/policy, structure-ийг ангилсан `RouterResult` гаргана. Follow-up асуултад зөвшөөрөгдсөн context ашиглан standalone `resolved_question` үүсгэнэ.

## Architecture Position

```mermaid
flowchart LR
    A["RequestContext"] --> B["Enhanced Router — one structured LLM call"]
    B --> C["RouterResult"]
    C --> D["Deterministic output validation"]
    D --> E["Agent Planner"]
    class A,E internal-link;
```

## Input

`RequestContext`: original/current question, recent conversation messages болон зөвшөөрөгдсөн context. Одоогийн runtime-д router сүүлийн 6 user/assistant message ашигладаг; older-turn candidate contract нь proposed.

## Processing

1. Mongolian Cyrillic, Latin transliteration, English, mixed-language болон typo-той асуултын утгыг ойлгох.
2. Шинэ/follow-up/clarification relationship тогтоох.
3. Бие даасан request-үүдийг салгаж, request тус бүрийн intent/policy санал болгох.
4. Follow-up-г standalone `resolved_question` болгон contextualize хийх.
5. Бүх semantic ажлыг нэг structured LLM output call-аар гаргах; Python талд output schema validate хийх.

## Output

Одоогийн `classify_intent()` нь `requests[]` дотор `intent`, `search_query`, `relationship`, `resolved_question` буцаадаг. Өргөтгөсөн `RouterResult` болон `related_turn_ids` зэрэг field-үүд proposed contract; runtime output гэж үзэхгүй.

## Responsibilities and Boundaries

Router нь retrieval strategy сонгохгүй, Vector Store search хийхгүй, evidence review эсвэл final answer үүсгэхгүй. [[Agent Planner]]-т semantic classification давтахгүйгээр хэрэгтэй information needs-ийг төлөвлөх validated request өгнө. LLM-ийн ангилал нь policy/security enforcement-ийг орлохгүй.

## Algorithm Overview

Дэлгэрэнгүй input/output, `RouterResult`, Router/Planner boundary: [[ER - Scope and contracts]].

## Detailed Documentation

- [[ER - Scope and contracts]]
- [[ER - Semantic normalization]]
- [[ER - Relationship and references]]
- [[ER - Intent and policy]]
- [[ER - Validation and edge cases]]

## Current Status

Одоогийн `classify_intent()` нэг structured LLM call-аар normalization, relationship, intent болон request decomposition хийдэг. Structured schema, request count, intent/relationship болон resolved question-ийн зарим output validation байна. Older-turn selection, өргөтгөсөн `RouterResult`, тусдаа technical input validation болон runtime normalization quality validation pending.

## Related Components

[[Question and history]] · [[Agent Planner]] · [[Select search strategy]] · [[Question taxonomy]]
