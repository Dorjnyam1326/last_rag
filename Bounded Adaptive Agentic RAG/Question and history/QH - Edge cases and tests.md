---
tags: [rag, tests, context, validation]
status: design-finalized-implementation-partial
---
# QH - Edge cases and tests

## Минимум acceptance checklist

- [x] **Original preserved**: хүчинтэй original question Router-д өөрчлөлтгүй дамжина (offline test).
- [x] **Window**: Router руу хамгийн ихдээ сүүлийн 6 detailed messages дамжина (offline test).
- [x] **Input guard**: non-text асуулт `error`, empty/whitespace-only асуулт `clarification` болж Router call хийхгүй (offline tests).
- [ ] **Chronological roles**: user/assistant роль болон дараалал алдагдахгүй.
- [ ] **Immediate follow-up**: `хэн зөвшөөрөх вэ?` → өмнөх чөлөөний turn.
- [x] **Older candidate catalogue**: Recent window-ээс гаднах хамгийн сүүлийн 8 user-question summary chronological байдлаар Router input-д орно (offline test). A/B/C/D/E дээр Router B-г зөв сонгох semantic чанар хараахан шалгагдаагүй.
- [ ] **New topic**: чөлөөнөөс ethics рүү шилжихэд өмнөх чөлөөний evidence-ийг reuse хийхгүй.
- [ ] **Same topic independent**: ижил HR topic ч өөр policy section байвал хүчээр холбохгүй.
- [ ] **Correction**: `4 биш 3 цаг` үед шинэ constraint-ийг хадгална.
- [ ] **Ambiguous referent**: хоёр боломжит хүсэлт байвал clarification.
- [ ] **Multi-intent**: R1/R2/R3 тусдаа related_turn_ids.
- [x] **Invalid candidate IDs**: Supplied allowlist-д байхгүй ID-г reject/clarification болгоно (offline tests). Future/cross-session authorization тусдаа шалгагдахгүй.
- [x] **Selected-turn isolation**: Сонгоогүй turn-ийн passages reuse болохгүй (offline pipeline test).
- [ ] **Deleted/outdated evidence**: хуучин version эсвэл missing file-г unquestioned truth гэж үзэхгүй.
- [ ] **No raw contamination**: irrelevance-тэй Top-5 raw passage history evidence болж орохгүй.
- [ ] **Citation identity**: `[D1]`-ийг stable file/chunk ID гэж ашиглахгүй.
- [ ] **Restricted data**: холбоос байгаа нь restricted question-д access олгохгүй.
- [ ] **Zero history**: анхны асуулт шинэ turn болж зөв дамжина.

## Failure modes

| Failure | Шалтгаан | Зөв арга |
|---|---|---|
| `E → D`, харин E нь B-г заасан | B candidate байхгүй / LLM mistake | Candidate catalogue ба link trace шалгах |
| No answer despite good context | Retrieval/generation failure | Context resolver-ийг буруутгахаас өмнө search log шалгах |
| Correct answer with wrong citation | Citation validation зөвхөн ID шалгасан | Statement-to-evidence review хэрэгтэй |
| Topic drift | Хуучин raw passages ихээр дахин орсон | Selected evidence + per-request filter |
| Token overflow | Бүх session history, chunk бүрийг илгээсэн | 6 detailed + bounded older summaries |

## Trace-д юу хадгалах вэ?

`session_id`, `current_turn_id`, `recent_turn_ids`, `older_candidate_ids`, `relationship`, `selected_related_turn_ids`, `resolved_question`, `reason_for_clarification`, `context_token_estimate`, `evidence_refs_loaded`.

Эдгээр нь **debug metadata**. Model-ийн private reasoning текстийг хадгалах шаардлагагүй.

## Testing scope

## Validation ба normalization test matrix (proposed)

Доорх матрицын ихэнх мөр regression test-ийн санал хэвээр. 2026-11-16-нд input guard, original-question preservation, six-message context, eight older-candidate bound, invalid candidate ID болон selected-turn passage isolation-ийн offline test-үүд ажилласан; бусад technical ба semantic case-уудыг хэрэгжүүлсэн гэж үзэхгүй. Semantic cases нь [[Enhanced Router]]-ийн behavior-ийг шалгана.

| Input | Expected behavior | Failure risk |
|---|---|---|
| Empty question: `""` | Input validation алдааг LLM call-аас өмнө буцаана | Хоосон request model руу явж, утгагүй response гаргах |
| Whitespace-only: `"   "` | Хоосон гэж таньж reject/clarify хийнэ | Trim-ийн дараа ч хоосон асуултыг боловсруулах |
| Unicode representation variation: `ө` precomposed/decomposed | NFC-д ижил canonical text болно; original хэвээр | Ижил текстийг өөр string гэж үзэх, эхийг гэмтээх |
| Хэт урт input | Тогтоосон limit-ийг шалгаж controlled error/clarification өгнө | Token өсөлт эсвэл provider limit алдаа |
| Кирилл: `3 цагийн чөлөө яаж авах вэ?` | Утга хэвээрээ normalized standalone question болно | Нөхцөл нэмэх, утгыг эвдэх |
| Монгол Latin: `3n tsagiin chuluu yaj avah ve?` | `3 цагийн чөлөө яаж авах вэ?` гэсэн утга | Буруу transliteration эсвэл actor/condition зохиох |
| English: `Who approves a three-hour leave request?` | Монгол кирилл утгыг хадгалсан resolved question | `three-hour`-ийг өөр тоо/нөхцөл болгох |
| Mixed: `3 tsagiin leave avmaar baina, who approves?` | Хоёр санааг хадгалсан нэг асуулт | Нэг хэсгийг орхих, intent салгалт алдах |
| Кирилл typo: `бйагууллгийн ... члөөг ...` | Context хүрвэл зөв бичгийн canonical meaning | Буруу үг тааж шинэ policy утга оруулах |
| Latin shorthand: `snu, chuluu yaj avah ve` | Greeting ба leave request-ийн утгыг хадгалах | `snu` зэрэг товчлолыг буруу тайлах |
| Follow-up: `tegeed hen approve hiih ve?` | Хүчинтэй history context-оор standalone асуулт болно | Хамааралгүй turn-ийг залгах |
| Topic switch | `new` гэж үзэж өмнөх context-ийг холихгүй | Өмнөх evidence шинэ сэдэвт нэвчих |
| Ambiguous reference: `Тэр хүсэлтийг хаана өгөх вэ?` | Нэг утгаар resolve хийх боломжгүй бол clarification | Буруу referent сонгох |
| Тоо: `3 цаг` | `3` хадгалагдана | Тоог өөрчлөх, хасах |
| Огноо/цаг: `15:00–18:00`, `24 цагийн өмнө` | Бүрэн утгаар хадгалагдана | Range, нэгж, хугацааг алдах |
| Үгүйсгэл: `Цалингүй чөлөө авч болохгүй юу?` | Үгүйсгэл ба асуултын polarity хадгалагдана | Эерэг эсрэг утга болгох |
| Acronym: `СХНТ`, `DMS`, `CAB` | Албан identifier хэвээр үлдэнэ | Identifier-ийг орчуулах/засах |
| Document code: `HR-10.4.5` | Яг хэвээр үлдэнэ | Кодын тэмдэгт/дугаар өөрчлөгдөх |
| Unsupported assumption: `3 цагийн чөлөө яаж авах вэ?` | `цалинтай` гэх хэлээгүй нөхцөл нэмэхгүй | Policy fact hallucinate хийх |

Эдгээр нь acceptance criteria болохоос implementation баталгаажуулалт биш. Output schema validation-ийг semantic preservation-ийн test гэж тооцохгүй.

Эхний ээлжинд хэдхэн шууд жишээгээр context parity шалгаад, өмнөх **10 асуултын regression**-ээр сайн ажиллаж байсан answer behavior өөрчлөгдөөгүйг харна. Энэ нь production-ийн бүх нөхцөлийг батлахгүй.

## Implementation status (as built)

- Verified offline: `tests.test_routing` 7 passed; 15 focused `AnswerQuestionTests` passed; full-history UI ID test 1 passed (2026-11-16).
- Broader check: `tests.test_pipeline.AnswerQuestionTests` 33 tests, 11 failures. Failures include older mock call assertions omitting `model=None`/`api_key=None`, a deduplication expectation missing `source_provenance`, and a multi-request mock citation missing `file_id`; candidate-specific focused tests passed. These unrelated assertions/fixtures were not rewritten as part of this phase.
- Remaining acceptance gaps: semantic A/B/C/D/E choice, ambiguous references, correction fidelity, per-request old-evidence isolation, session ownership, and reviewed-evidence provenance.

## Related

[[QH - Worked examples]] · [[QH - Context candidates]] · [[Enhanced Router]] · [[QH - Design decisions]]
