---
tags: [rag, evidence, provenance, citations]
status: design-finalized-implementation-partial
---
# QH - Evidence and provenance

## Асуудал: Raw passages vs selected evidence

Одоогийн Streamlit замд `result["passages"][:5]` history-д хадгалагдаж байгаа. Follow-up үед Router сонгосон turn-ийн эдгээр raw passages л pipeline-д авч reviewer дахин шалгана; `result["evidence"]` history-д хадгалагдахгүй. Raw Top-5 дотор хамгийн өндөр score-той **IT change management** passage ч чөлөөний асуултад орж ирсэн жишээ байгаа.

**Target design:** өмнөх turn-ээс **review-д хамааралтай гэж сонгогдсон evidence**-ийг source identity-тай нь хадгалах. Хариултад cited болсон эсэхийг тусдаа тэмдэглэнэ.

| Field | Зорилго |
|---|---|
| `file_id` | OpenAI-д байгаа file identity |
| `filename` | UI-д харагдах нэр; identity-д дангаар найдахгүй |
| `document_version` | Боломжтой бол version/effective metadata |
| `chunk_id` эсвэл `text_fingerprint` | Chunk identity / exact dedup-д |
| `text` | Reviewer-д дахин өгөх боломжтой evidence |
| `reviewed_relevance` | Relevant эсэх / ямар fact дэмжсэн |
| `cited_in_answer` | Эцсийн answer-д ашигласан эсэх |
| `request_id` | R1/R2/R3 аль хүсэлтэд хамаарах |

## Citation display ID ≠ permanent identity

`[D1]`, `[D2]` нь тухайн generation turn-д оноогдох **түр identifier** байж болно. Follow-up storage-д `[D1]`-ийг байнгын chunk key болгож болохгүй. `file_id + stable chunk identity + version` зэрэг таних мэдээлэл хэрэгтэй.

```mermaid
flowchart TD
    A["Retrieved raw passages"] --> B["Evidence review"]
    B --> C["Selected evidence + provenance"]
    C --> D["Grounded answer citations"]
    C --> E["Store per-turn evidence references"]
    E --> F["Future follow-up: candidate evidence"]
    F --> G["Review again against new question"]
```

## Яагаад дахин review хийх вэ?

- Өмнөх answer буруу байж болно; answer текст policy source биш.
- Хуучин evidence шинэ асуултад тохирохгүй байж болно.
- Баримтын хувилбар солигдсон бол хуучин content obsolete байж болно.
- Citation зөв ID-тай байлаа ч өгүүлбэрийг агуулгын хувьд батлахгүй байж болно.

## Loading policy

Router `related_turn_ids` сонгосны дараа одоогийн код тухайн turn-д хадгалсан bounded raw passages-ийг шинэ retrieval-ийн хамт reviewer-т өгдөг. **Энэ нь selected evidence-ийг хадгалахтай адил биш**; generation өмнөх answer-ийг эх сурвалж гэж шууд итгэмжлэхгүй.

## Implementation status (as built)

- Status: partial. `answer_question()` зөвхөн selected turn ID-уудын assistant message-ээс passages авч, eligibility/type/text-ийг `previous_turn_passages()`-аар шалгаж, deduplicate хийн reviewer-т дахин өгнө.
- Verified: `tests.test_pipeline.AnswerQuestionTests.test_follow_up_reuses_passages_only_from_selected_older_turn` сонгосон older candidate turn-ийн passage орж, сүүлийн хамааралгүй turn-ийн passage хасагдахыг батална.
- Limits: UI raw passages хадгалдаг; reviewed evidence, stable chunk/version identity, citation-to-evidence provenance, per-request evidence isolation байхгүй. Хуучин source eligibility-г зөвхөн одоогийн eligible file IDs өгсөн тохиолдолд дахин шалгана.

Multi-intent message дээр evidence record бүр `request_id`-тай; restricted R3-ийн source-ийг allowed R1-ийн context-д оруулахгүй.

## Related

[[Question and history]] · [[QH - RequestContext contract]] · [[Dedup and Evidence review]] · [[Grounded answer and citations]]
