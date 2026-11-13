# Phase 8–9 progress checkpoint

## Completed

- Phase 8 evidence review, bounded retry, and partial-answer handling are implemented. The pipeline searches at most twice per allowed request and retains useful first-search evidence if retry review fails.
- Phase 9 live evaluation completed against the configured existing Vector Store. The run used `gpt-6-luna` on the Standard tier, with a 1,200-token output cap, 45-second timeout, and SDK retries disabled. No files were uploaded or modified.
- The dataset has 30 unique cases (24 development, 6 holdout). There were 33 end-to-end attempts including targeted reruns. Holdout cases were not used for prompt changes.
- Final fixture checks: 30/30 case request counts, 34/34 intent labels, 35/35 relationships, and 35/35 expected actions matched. These are objective fixture checks, not answer-quality labels.
- Live calls: 33 router, 58 evidence-review, 30 generation, and 58 Vector Store searches; 27 were conditional retry searches. No provider or pipeline errors were recorded.
- Token usage is available for 120/121 model calls: 295,134 input and 27,009 output tokens recorded. The first C01 router attempt was overwritten before its token record could be preserved; a separate accounting entry estimates that call. Cumulative estimated cost is `$0.04754904`, including a 10% regional buffer and the missing-call estimate, below the `$15` internal stop threshold. This is an estimate, not an account invoice.
- The router now sends bounded prior turns as a JSON `history_context` field and the current user request separately as `current_question`. Live C23 retest routed only the new confidential-document question; the earlier leave turn did not become a second request.
- The ignored local trace is `evaluation/live_results.json`; the proposed review sheet is `evaluation/human_review.csv`. Retrieved passage text is not stored there. No answer/citation has verified source-ground-truth labels.

## Observed results and limits

- Final outputs across 35 routed requests: 5 `answered`, 20 `partially_answered`, 2 `insufficient`, 5 `restricted`, 1 `clarification`, 1 `greeting`, and 1 `out_of_scope`.
- Evidence reviewer outcomes in final case traces: 6 `sufficient`, 32 `partial`, 13 `insufficient`. These are model judgments, not human confirmation that a passage supports a policy claim.
- The first smoke run classified C01 as out of scope. A focused workplace-scope prompt correction made its targeted rerun pass the expected route/action check. The full evaluation also exposed C23 history leakage; a focused payload-boundary change made its final rerun pass.
- No Hit@5, MRR, answer correctness, or citation-support score is claimed because the expected source passages and answers have not been verified by a human reviewer. `evaluation/human_review.csv` contains the final answers/citations and blank review fields for that work.
- Previous LangChain results used different runs/scenarios and were not paired to this 30-case dataset or confirmed document versions; use them only as a non-equivalent architecture/latency reference, not as a quality-improvement claim.
- The core remains a prototype. Production integration still requires an approved/current-document retrieval constraint and human review of policy answers and citations.
