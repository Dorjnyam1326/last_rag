import json
from uuid import uuid4

import streamlit as st
from openai import OpenAIError

from rag.pipeline import answer_question
from rag.retrieval import MAX_REUSABLE_PASSAGES


def _render_citations(citations: list[dict]) -> None:
    if not citations:
        return
    with st.expander(f"Эх сурвалж · {len(citations)}"):
        for citation in citations:
            request = (
                f" · {citation['request_id']}" if citation.get("request_id") else ""
            )
            st.caption(f"{citation['source_id']}{request} · {citation['filename']}")


def _ensure_turn_ids(history: list[dict]) -> None:
    active_turn_id = None
    for message in history:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            turn_id = message.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id.strip():
                turn_id = uuid4().hex
                message["turn_id"] = turn_id
            active_turn_id = turn_id
        elif message.get("role") == "assistant" and active_turn_id:
            if message.get("turn_id") != active_turn_id:
                message["turn_id"] = active_turn_id


def _render_single_result(result: dict) -> None:
    if result["status"] == "partially_answered":
        st.badge("Хэсэгчилсэн хариулт", color="orange")
        st.markdown(result["answer"])
        _render_citations(result["citations"])
    elif result["status"] == "answered":
        st.markdown(result["answer"])
        _render_citations(result["citations"])
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


def _render_result(result: dict) -> None:
    if result.get("results"):
        for item in result["results"]:
            with st.container(border=True):
                st.caption(f"{item['request_id']} · {item['question']}")
                _render_single_result(item)
    else:
        _render_single_result(result)


def render_app() -> None:
    st.set_page_config(
        page_title="Ажлын байрны бодлогын туслах",
        page_icon=":material/chat:",
        layout="centered",
    )
    st.session_state.setdefault("chat_history", [])
    history = st.session_state.chat_history
    _ensure_turn_ids(history)

    st.title("Ажлын байрны бодлогын туслах", icon=":material/chat:")
    st.caption(
        "Хариулт нь баримтад тулгуурлана. Эх сурвалжийг хариултын доороос нээнэ үү."
    )

    with st.sidebar:
        st.subheader("Яриа")
        st.metric("Асуулт", sum(message["role"] == "user" for message in history))
        export = [
            {
                key: message[key]
                for key in ("role", "content", "citations", "status", "results")
                if key in message
            }
            for message in history
        ]
        st.download_button(
            "Яриаг JSON-ээр татах",
            data=json.dumps(export, ensure_ascii=False, indent=2),
            file_name="rag-conversation.json",
            mime="application/json",
            disabled=not history,
            width="stretch",
        )
        if st.button("Яриаг цэвэрлэх", width="stretch", disabled=not history):
            history.clear()
            st.rerun()
        st.caption("Яриа зөвхөн энэ Streamlit session-д хадгалагдана.")

    suggestions = {
        "Чөлөөний журам": "Чөлөө авах хүсэлтийг хэрхэн гаргах вэ?",
        "Ажлын цаг": "Ажлын цаг болон цаг бүртгэлийн журам юу вэ?",
        "Нөхөн төлбөр": "Зардлын нөхөн төлбөр авахын тулд юу хийх вэ?",
    }
    selected = None
    if not history:
        selected = st.pills(
            "Жишээ асуулт", list(suggestions), label_visibility="collapsed"
        )

    for message in history:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                previous = {
                    "answer": message["content"],
                    "citations": message.get("citations", []),
                    "status": message.get("status", "answered"),
                    "passages": [],
                }
                if message.get("results"):
                    previous["results"] = [
                        {**item, "passages": []} for item in message["results"]
                    ]
                _render_result(previous)
            else:
                st.markdown(message["content"])

    question = st.chat_input("Асуултаа бичнэ үү")
    if selected:
        question = suggestions[selected]

    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Баримт хайж, хариулт боловсруулж байна…"):
                    result = answer_question(
                        question,
                        history=history,
                    )
            except RuntimeError:
                st.error("Vector Store-ийн тохиргоог шалгана уу.")
                return
            except OpenAIError:
                st.error(
                    "OpenAI хайлт амжилтгүй боллоо. Тохиргоо болон холболтоо шалгана уу."
                )
                return
            except Exception:
                st.error("Хариулт боловсруулахад алдаа гарлаа. Дахин оролдоно уу.")
                return
            _render_result(result)

        turn_id = uuid4().hex
        assistant_message = {
            "role": "assistant",
            "content": result["answer"],
            "turn_id": turn_id,
            "citations": result["citations"],
            "status": result["status"],
            "passages": result.get("passages", [])[:MAX_REUSABLE_PASSAGES],
        }
        if result.get("results"):
            assistant_message["results"] = [
                {**item, "passages": []} for item in result["results"]
            ]
        st.session_state.chat_history = history + [
            {"role": "user", "content": question, "turn_id": turn_id},
            assistant_message,
        ]
