# Minimal Mongolian RAG

A small, framework-independent RAG Core with a separate Streamlit development
UI. The public entry point is `rag.pipeline.answer_question(question, history=None)`.
It returns an answer, status, citations, retrieved passages, a process trace for
the development UI, and grouped results when the router finds multiple
independent requests.

## Request flow

```text
app.py → ui/streamlit_ui.py → rag.pipeline
                            → semantic routing and restrictions
                            → OpenAI Vector Store retrieval
                            → evidence review, one bounded retry if useful
                            → grounded Mongolian generation
```

The RAG Core does not import Streamlit and does not persist conversation
history. The development UI keeps only bounded temporary history in session
state. Evidence review can return sufficient, partial, or insufficient; retry
is limited to one distinct query per allowed request. No document upload,
indexing, or Vector Store mutation is performed by the chat flow.

Each answer also has an expandable **Боловсруулалтын явц** view with the
observed Router, policy, Planner, SSS, Retrieval, deduplication, evidence review,
retry, generation, and citation-grounding outputs. It shows operational inputs and
results, not hidden model reasoning or prompts; the trace remains in the
current Streamlit session.

## Configuration

Copy `.env.example` to `.env` and set `OPENAI_API_KEY` and
`OPENAI_VECTOR_STORE_ID`. `OPENAI_MODEL` configures routing, evidence review,
and generation. Do not commit `.env`.

## Run locally

This project uses `.venv312`:

```powershell
.venv312\Scripts\python.exe -m streamlit run app.py
```

## Offline verification

Run the focused test suite:

```powershell
.venv312\Scripts\python.exe -m unittest tests.test_routing tests.test_retrieval tests.test_generation tests.test_pipeline tests.test_streamlit_ui
```

Validate the frozen 30-case evaluation fixtures and deterministic restriction
labels without provider calls:

```powershell
.venv312\Scripts\python.exe -m evaluation.run_offline
```

The offline evaluator does not measure live model quality or source-grounded
answer accuracy. See [evaluation/README.md](evaluation/README.md).
