---
tags: [rag, context, data-model]
status: proposed-contract
---
# QH - Message and turn model

## `message` vs `turn` vs `request`

Нэг session дотор олон **turn** байна. Нэг turn-ийг user-ийн асуулт болон түүнд өгсөн assistant-ийн хариулт гэж үзэж болно. Нэг user message нь олон independent **request** (R1, R2, R3) агуулж болно.

```mermaid
flowchart TD
    S["session_12"] --> T1["turn_101"]
    S --> T2["turn_102"]
    T1 --> U1["user message"]
    T1 --> A1["assistant message"]
    T2 --> U2["user message"]
    T2 --> A2["assistant message"]
    U2 --> R1["request R1"]
    U2 --> R2["request R2"]
```

| ID / Field | Үүрэг | Тэмдэглэл |
|---|---|---|
| `session_id` | Хэлэлцүүлгийн хүрээ | Нэг session-ээс нөгөөг fetch хийхгүй |
| `turn_id` | Нэг user turn-ийн тогтвортой ID | History link-ийн үндсэн anchor |
| `message_id` | User/assistant message-ийн ID | Дэлгэцийн дараалал, role |
| `request_id` | Turn доторх R1/R2/R3 | Multi-intent isolation |
| `topic_id` | Ерөнхий сэдвийн бүлэг | Ижил topic = заавал follow-up биш |
| `related_turn_ids` | Өмнөх turn-тай холбоо | Router санал; код verify хийнэ |
| `original_question` | Хэрэглэгчийн яг бичсэн текст | Rewrite хийхгүй |
| `resolved_question` | Context-оор бүрэн болгосон асуулт | Router-ийн output |
| `answer` / `evidence_refs` | Өмнөх result ба provenance | Хариулт нь authoritative source биш |

## Жишээ: нэг turn → олон request

```json
{
  "session_id": "s12",
  "turn_id": "t105",
  "user_message_id": "m207",
  "question": "Цалин хэзээ буудаг, чөлөө яаж авдаг вэ?",
  "requests": [
    {"request_id": "R1", "intent": "payday", "related_turn_ids": []},
    {"request_id": "R2", "intent": "leave_procedure", "related_turn_ids": ["t101"]}
  ]
}
```

`requests[].related_turn_ids` нь нэг turn-ийн бүх sub-request-ийг ижил history-д хүчээр холбоход хүргэхгүй.

## Хадгалалтын түвшин

- `refactor`: эхлээд Streamlit `session_state`-ийн turn records, **DB шаардлагагүй**.
- Production систем: хэзээ нэгтгэхээр шийдвэл өмнөх existing AskLog/session-ийн ID дээр концепцоор mapping хийж болно. **Одоогийн RAG Admin-д schema эсвэл код өөрчлөхгүй**.
- ID-г зөвхөн local session-ийн namespace-д тогтвортой ашиглах; ID-г асуултын текстээс тооцохгүй.

## Related

[[Question and history]] · [[QH - Conversation links]] · [[QH - RequestContext contract]]
