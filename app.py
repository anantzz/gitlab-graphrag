"""Streamlit UI (local only):  streamlit run app.py"""
import graphrag_gitlab  # noqa: F401  (applies the telemetry lockdown before anything else is imported)
import streamlit as st

from graphrag_gitlab.agent.graph import CodeAssistant
from graphrag_gitlab.agent.llm import make_llm
from graphrag_gitlab.config import Settings
from graphrag_gitlab.index.store import CodeIndex
from graphrag_gitlab.privacy import check_endpoints

st.set_page_config(page_title="GitLab Code Assistant", layout="wide")


@st.cache_resource
def load_assistant():
    settings = Settings()
    check_endpoints(settings)
    index = CodeIndex.load(settings.index_dir)
    return CodeAssistant(index, make_llm(settings), top_k=settings.top_k, max_context=settings.max_context_chunks,
                         max_snippet_lines=settings.max_snippet_lines, use_llm_planner=settings.use_llm_planner,
                         num_ctx=settings.num_ctx), settings


assistant, settings = load_assistant()
index = assistant.index

with st.sidebar:
    st.header("Index")
    stats = index.stats()
    st.metric("Repositories", len(stats["repos"]))
    st.metric("Code chunks", sum(stats["chunks"].values()))
    st.metric("Graph edges", sum(stats["edges"].values()))
    repo_filter = st.multiselect("Limit to repositories", list(index.repos))
    show_trace = st.checkbox("Show agent trace", value=False)
    st.caption(f"Model: {settings.ollama_model} (local)" if assistant.llm else "Mode: retrieval only")

st.title("GitLab Code Assistant")
st.caption("Runs entirely on this machine. Ask where functionality lives, how something works, "
           "or which repositories cover a requirement.")

examples = [
    "Where is authentication implemented?",
    "Which files are related to payment processing?",
    "How does the order placement flow work?",
    "Which repositories send email notifications?",
]
cols = st.columns(len(examples))
for col, ex in zip(cols, examples):
    if col.button(ex, use_container_width=True):
        st.session_state["q"] = ex

question = st.text_input("Question", key="q", placeholder="e.g. How does token refresh work?")
if question:
    box = st.empty()
    streamed: list[str] = []

    def on_token(t: str) -> None:
        streamed.append(t)
        box.markdown("".join(streamed) + " ▌")

    with st.spinner("Searching code and traversing the graph… (first answer can take a minute on CPU)"):
        result = assistant.ask(question, repo_filter, on_token=on_token)
    box.markdown(result["answer"])
    if result.get("llm_note"):
        st.warning(result["llm_note"])
    if show_trace:
        st.code("\n".join(result.get("trace", [])))

    st.subheader("Sources")
    for s in result.get("sources", []):
        if not s.get("cited"):
            continue
        title = f"[S{s['n']}] {s['repo']}/{s['path']} · {s['qualname']} (L{s['start_line']}-{s['end_line']})"
        with st.expander(title):
            if s["url"]:
                st.markdown(f"[Open in GitLab]({s['url']})")
            st.caption(f"{s['kind']} · found via {s['via']}: {', '.join(s['reasons'][:3])}")
            st.code(s["code"], language=s["language"] if s["language"] not in ("text", "markdown") else None)
