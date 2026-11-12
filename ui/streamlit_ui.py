import streamlit as st
from openai import OpenAIError

from rag.pipeline import answer_question


def render_app() -> None:
    st.title("RAG Chatbot")

    question = st.chat_input("Асуултаа бичнэ үү")

    if question:
        try:
            results = answer_question(question)
        except RuntimeError:
            st.error("Vector Store-ийн тохиргоог шалгана уу.")
            return
        except OpenAIError:
            st.error("OpenAI хайлт амжилтгүй боллоо. API тохиргоо болон холболтоо шалгана уу.")
            return

        if not results:
            st.info("Хайлтаар тохирох баримт олдсонгүй.")
            return

        for result in results:
            with st.container(border=True):
                st.subheader(result["filename"])
                if result.get("score") is not None:
                    st.caption(f"Хамаарлын оноо: {result['score']}")
                st.text(result["text"])
