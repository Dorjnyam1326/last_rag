import streamlit as st
from openai import OpenAIError

from rag.pipeline import answer_question
from rag.routing import MAX_HISTORY_MESSAGES


def _render_result(result: dict) -> None:
    if result["status"] == "answered":
        st.markdown(result["answer"])
        if result["citations"]:
            st.markdown("**Эх сурвалж**")
            for citation in result["citations"]:
                st.caption(f"{citation['source_id']} · {citation['filename']}")
    elif result["status"] in {"insufficient", "out_of_scope", "clarification"}:
        st.info(result["answer"])
    elif result["status"] == "restricted":
        st.warning(result["answer"])
    elif result["status"] == "greeting":
        st.write(result["answer"])
    else:
        st.error(result["answer"])

    if result["passages"]:
        with st.expander("Татсан баримтын хэсгүүд"):
            for passage in result["passages"]:
                st.caption(passage["filename"])
                if passage.get("score") is not None:
                    st.caption(f"Хамаарлын оноо: {passage['score']}")
                st.text(passage["text"])


def render_app() -> None:
    st.title("RAG Chatbot")
    st.session_state.setdefault("chat_history", [])

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                _render_result(
                    {
                        "answer": message["content"],
                        "citations": message.get("citations", []),
                        "status": message.get("status", "answered"),
                        "passages": [],
                    }
                )
            else:
                st.markdown(message["content"])

    question = st.chat_input("Асуултаа бичнэ үү")

    if question:
        history = st.session_state.chat_history[-MAX_HISTORY_MESSAGES:]
        try:
            result = answer_question(question, history=history)
        except RuntimeError:
            st.error("Vector Store-ийн тохиргоог шалгана уу.")
            return
        except OpenAIError:
            st.error("OpenAI хайлт амжилтгүй боллоо. API тохиргоо болон холболтоо шалгана уу.")
            return

        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            _render_result(result)

        st.session_state.chat_history = (
            history
            + [{"role": "user", "content": question}]
            + [
                {
                    "role": "assistant",
                    "content": result["answer"],
                    "citations": result["citations"],
                    "status": result["status"],
                }
            ]
        )[-MAX_HISTORY_MESSAGES:]
