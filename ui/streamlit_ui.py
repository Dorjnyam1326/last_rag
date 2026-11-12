import streamlit as st
from openai import OpenAIError

from rag.pipeline import answer_question


def render_app() -> None:
    st.title("RAG Chatbot")

    question = st.chat_input("Асуултаа бичнэ үү")

    if question:
        try:
            result = answer_question(question)
        except RuntimeError:
            st.error("Vector Store-ийн тохиргоог шалгана уу.")
            return
        except OpenAIError:
            st.error("OpenAI хайлт амжилтгүй боллоо. API тохиргоо болон холболтоо шалгана уу.")
            return

        if result["status"] == "answered":
            st.markdown(result["answer"])
            if result["citations"]:
                st.markdown("**Эх сурвалж**")
                for citation in result["citations"]:
                    st.caption(f"{citation['source_id']} · {citation['filename']}")
        elif result["status"] == "insufficient":
            st.info(result["answer"])
        elif result["status"] == "restricted":
            st.warning(result["answer"])
        elif result["status"] == "out_of_scope":
            st.info(result["answer"])
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
