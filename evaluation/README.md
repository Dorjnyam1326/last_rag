# RAG evaluation fixtures

`cases.json` contains exactly 30 frozen questions: 24 development cases and 6
holdout cases. Expected labels cover routing, request relationships, and local
restriction outcomes. No case contains a source-verified company-policy answer;
do not interpret the fixture as answer-quality ground truth or tune against the
holdout split.

Run the offline fixture and deterministic-policy audit from the repository root:

```powershell
.venv312\Scripts\python.exe -m evaluation.run_offline
```

This command makes no provider calls. It validates the fixture schema/counts and
checks expected restriction outcomes against the current Python registry. It
does not measure model routing, normalization, retrieval, or generation quality.

## Opt-in live evaluation

The live runner uses the configured `gpt-6-luna` model on the Standard tier,
limits each model response to 1,200 tokens, uses 45-second request timeouts,
disables SDK retries, and stops before a case if the conservative cumulative
estimate would exceed `$15`. It writes detailed answers and metadata to ignored
local files (`live_results.json` and `human_review.csv`) and does not store
retrieved passage text.

Run the eight-case development smoke set first:

```powershell
.venv312\Scripts\python.exe -m evaluation.run_live --confirm-live
```

After reviewing the smoke results, continue with all cases, including the six
holdout cases. Completed cases are skipped when the trace already exists:

```powershell
.venv312\Scripts\python.exe -m evaluation.run_live --all-cases --confirm-live
```

After a confirmed development-case fix, rerun only that case with:

```powershell
.venv312\Scripts\python.exe -m evaluation.run_live --case-ids C01 --rerun-cases C01 --confirm-live
```

The JSON records token usage when returned by the model and a conservative cost
upper bound. The CSV is a proposed human-review sheet; no policy-answer label is
considered verified until an authorized reviewer checks the indexed documents.
