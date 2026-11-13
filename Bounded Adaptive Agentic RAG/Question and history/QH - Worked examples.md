---
tags: [rag, context, examples]
status: examples
---
# QH - Worked examples

Эдгээр нь алгоритмын **expected behavior**-ийн жишээ; одоогийн код бүгдийг нь хийсэн гэсэн баталгаа биш.

## Example A — Immediate follow-up

**History:**

- t1 User: `3n tsagiin chuluu awmaar baina`
- t1 Assistant: `8 цаг хүртэлх чөлөөг Хэлтсийн менежер зөвшөөрнө ...`

**Current:** `tegeed hen medegdeh yostoi ve?`

**Expected Router:** `follow_up`, `related_turn_ids=[t1]`, `resolved_question="3 цагийн чөлөө авахдаа хэнд мэдэгдэх ёстой вэ?"`.

## Example B — Older-topic reference (A/B/C/D/E)

| Turn | Question | Topic |
|---|---|---|
| A/t101 | 3 цагийн чөлөө яаж авах вэ? | leave |
| B/t102 | Хэн зөвшөөрөх вэ? | leave |
| C/t103 | Ёс зүйн зөрчил гэж юу вэ? | ethics |
| D/t104 | Давж заалдах хугацаа? | ethics |
| E/t105 | `manager baihgui bol hen zovshooroh ve?` | ? |

**Expected:** candidate catalogue-д B байгаа бол Router `related_turn_ids=[t102]` (шаардлагатай бол t101) сонгоно. D-ийн ethics evidence-ийг E-д оруулахгүй.

## Example C — Same topic, independent question

History: `3 цагийн чөлөө яаж авах вэ?`

Current: `Ээлжийн амралт хэд хоног үргэлжилдэг вэ?`

Expected: өмнөх 3 цагийн чөлөөний хариултгүйгээр утга нь бүрэн тул `new` + optional `same_topic_independent`; previous evidence автоматаар reuse хийхгүй.

## Example D — Correction

History: `4 цагийн цалинтай чөлөө авах уу?`

Current: `үгүй 4 биш, 3 цаг`

Expected: өмнөх intent-ийг зассан follow-up, `resolved_question` нь **3 цагийн** чөлөөний тухай; өмнөх 4 цагийн нөхцөлийг unchanged ашиглахгүй.

## Example E — Ambiguous

History-д `чөлөөний хүсэлт`, `цалин нэмүүлэх хүсэлт` хоёр бий.

Current: `Тэр хүсэлтийг хаана илгээх вэ?`

Expected: ямар request болох нь ambiguity-тэй бол `clarification`; мэдэхгүй холбоосыг зохиохгүй.

## Example F — Mixed-language multi-intent

Current: `payday hed ve bas manager baihgui bol leave hen approve hiih ve?`

Expected: R1 `payday` (шинэ), R2 `leave approval` (older leave turn-тэй холбогдож болно). R1/R2 өөр `related_turn_ids`, evidence, policy route-тай.

## Example G — Insufficient source

Current: `Хаалганы чип алдагдвал хүсэлтээ хаана илгээх вэ?`

Expected: context resolve амжилттай байсан ч source-д журам олдохгүй байж болно. **Reference resolution зөв** гэдэг нь **evidence sufficient** гэсэн үг биш.

## Related

[[QH - Context candidates]] · [[QH - Conversation links]] · [[QH - Edge cases and tests]] · [[Enhanced Router]]
