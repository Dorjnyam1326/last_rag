# RAG application

A small Python application that connects a Streamlit question input to a RAG
pipeline function. The pipeline currently returns a temporary echo response.

## Project structure

```text
app.py                 Python entry point
ui/
  streamlit_ui.py      Streamlit design and interaction only
rag/
  pipeline.py          Receives a question and returns an answer
tests/                 Automated tests
requirements.txt       Python dependencies
```

## Run locally

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```

Run `streamlit run app.py` to start the app. Type a question to see the
temporary echo response. No model or retrieval calls are made yet. New RAG
modules will be added as each development step needs them.
