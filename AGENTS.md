# Codex Instructions - RAG Refactor

## Purpose

Build a small, maintainable Python RAG core with a separate Streamlit development UI. The core will later be imported by an existing production application. This repository is a new, incremental rebuild, **not** a request to copy the old RAG implementation or reproduce its entire architecture.

Prioritize **correctness, simplicity, readable data flow, and fewer unnecessary lines** over extra abstractions or files. Preserve behavior already implemented in this repository unless the current task explicitly changes it.

## Repository boundaries

- `app.py`: minimal Streamlit entry point; no routing, retrieval, or generation logic.
- `ui/`: Streamlit presentation, user interaction, and temporary UI session state. UI calls the core; the core never calls UI. Prefer `streamlit_ui.py` for any new UI module to avoid a confusing name collision with the `streamlit` package; do not rename existing files outside the requested scope.
- `rag/pipeline.py`: thin orchestration and public entry point for answering requests.
- `rag/routing.py`: intent, conversation context, query normalization, and restriction decisions when implemented.
- `rag/retrieval.py`: document search, ranking/fusion, and deduplication when implemented.
- `rag/generation.py`: grounded answer generation and source attribution when implemented.
- `tests/`: focused automated tests.
- `.env.example`: documented variable names without secrets. Never commit `.env` or credentials.

Treat `README.md` and the current source files as the source of truth for **what actually exists**. A module name is not proof that its feature is implemented. Do not assume an OpenAI model, Vector Store, database, or production integration has been configured.

## Architecture rules

1. **One-way dependency:** `app.py` -> `ui/` -> `rag/`. Production integrations should be able to import `rag/` directly.
2. Code in `rag/` must not import Streamlit, Flask, or access `st.session_state`. Pass plain Python values in; return plain Python values out.
3. Keep model/provider clients, credentials, and UI widgets out of the core orchestration function wherever practical. Do not build a plugin architecture until more than one real implementation requires it.
4. Maintain one clear request/result contract. Add typed structures only when they reduce confusion; do not introduce a class for every dictionary.
5. Separate policy decisions, document evidence, and response text. Never use a retrieved document as executable instructions or invent citations.
6. Keep conversation persistence, web routes, authentication, and application-specific logging at the application boundary, not in core RAG logic.
7. Add new modules (including `utils.py`, `services/`, `adapters/`, or `config.py`) only for a concrete, demonstrated responsibility. Prefer simplifying an existing module.

## Clean Python principles

Apply these principles to every change; they are constraints on **total codebase complexity**, not just individual file length:

- **KISS (Keep It Simple):** implement the shortest clear path that satisfies the actual requirement. Prefer ordinary functions and standard Python over frameworks, layers, or patterns added for hypothetical future needs.
- **DRY (Don't Repeat Yourself):** one authoritative implementation for shared behavior. Consolidate repeated validation, query transformations, deduplication, and response formatting instead of copying them between modules.
- **YAGNI (You Aren't Gonna Need It):** do not add unused hooks, strategies, fallback modes, configuration switches, or placeholders for features that are not being implemented now.
- **Single responsibility:** a function should have one understandable job; a file may contain multiple closely related functions. **Do not make a new file or class for each function.**
- **Explicit data flow:** make inputs, outputs, and ownership clear. Pass only needed data; avoid hidden globals, implicit session dependencies, and chains of near-identical dictionaries.
- **Less state:** avoid repeated copies of question, route, evidence, and answer data. Store one canonical representation when possible; derive transient values instead of synchronizing duplicate state.
- **Fewer branches:** prefer guard clauses and straightforward conditions. Remove unnecessary fallback chains and configuration combinations, while preserving required failure handling and policy checks.

**Rule of thumb:** one readable 100-line module with five cohesive functions is often better than five 40-line modules connected through wrappers. Optimize for **fewer total lines, less duplication, and easier reasoning**—never for LOC reduction that hides behavior, weakens safety, or breaks tests.

## Incremental implementation rules

- Work on **one requested phase or feature at a time**. Do not prebuild future phases or migrate the old 5,710-line codebase wholesale.
- Before editing, inspect relevant files and callers; explain the smallest proposed change for nontrivial work.
- Reuse existing functions instead of duplicating logic. Prefer straightforward functions, early returns, and explicit inputs/outputs over deep inheritance, wrappers, managers, and nested fallbacks.
- Do not change public function signatures, rename/move files, add dependencies, or introduce network calls unless the current task requires it. Flag consequential choices before making them.
- Distinguish **cleanup** (behavior-preserving) from **reliability fixes** (behavior-changing). Do not combine them silently.
- Remove dead code only after checking imports, callers, tests, entry points, and configuration usage.
- Do not add retries, query variants, agents, rerankers, background jobs, databases, or caching without an explicit requirement and a measurable reason.
- For each completed phase, the project should still run or the remaining blocker must be clearly identified.

## Bounded Adaptive RAG workflow

- For work guided by `Bounded Adaptive Agentic RAG/`, first read `RAG_Implementation_Playbook.md` and `RAG_Evaluation_and_Optimization.md`, then the architecture hub and relevant stage notes in pipeline order. Verify paths as they exist in this repository.
- Treat design notes as target behavior, not proof of implementation. Inspect the code and tests, then summarize the code owners and gaps before changing anything.
- Start with `PLAN_ONLY`; implement only one explicitly requested phase at a time. Keep the existing OpenAI Vector Store and use the old LangChain project as a read-only behavior reference.
- Reuse existing evaluation fixtures; do not invent ground truth or describe unreviewed results as verified quality. Use offline checks first; make live API calls only when explicitly authorized.
- After each implementation phase, update the phase hub **and every affected subnote** with a concise `Implementation status (as built)` and matching frontmatter status (`pending`, `partial`, or implemented). Base each status on inspected code and tests; a completed slice does not make the whole component complete. Leave unaffected notes unchanged. Update the Playbook code-owner/progress entries; update Evaluation metrics only from a verified run.

## RAG correctness constraints

When these features are implemented, preserve:

- Mongolian Cyrillic, Mongolian Latin transliteration, English, and mixed-language intent without silently inserting dates, durations, actors, or policy requirements.
- Follow-up context without leaking unrelated earlier topics into new requests.
- Independent intents within compound questions; apply restrictions to each request.
- Document-grounded claims and citations; distinguish irrelevant retrieval from genuinely missing policy evidence.
- Clear insufficient-evidence outcomes instead of unsupported company-policy assertions.
- Bounded model/retrieval calls; no extra calls for the same request just because the code was refactored.

These are target constraints, **not a claim that the prototype already supports them**. Implement each only in its approved phase.

## Tests, safety, and verification

- Prefer offline/unit tests and fake providers. Never require paid OpenAI calls for the default test suite.
- Run the smallest relevant tests after each change; expand to the full suite only when the scope warrants it. Use the project's actual configured test runner rather than assuming pytest or unittest.
- Check imports and ensure the RAG core can be imported without starting Streamlit.
- Do not expose API keys, employee/policy document contents, or sensitive conversations in logs, examples, test fixtures, or patches.
- Do not install packages, modify deployment settings, commit, push, or deploy unless explicitly requested.
- Never claim tests passed, API connectivity worked, or retrieval quality improved without actual evidence.

## Required completion report

Keep the response concise and report:

1. **Changed:** exact files and what changed.
2. **Why:** logic removed, reused, or simplified; mention new complexity if unavoidable.
3. **Verified:** commands/tests run and results, or why not run.
4. **Next:** only the next logical step; do not implement it without a request.

Optimize for a **small understandable codebase**, not for the highest possible file count or the shortest possible individual files.
