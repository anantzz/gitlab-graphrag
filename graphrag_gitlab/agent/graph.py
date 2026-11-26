"""LangGraph workflow: analyze → retrieve (hybrid) ⇄ rewrite → expand (graph) → generate (cited answer).

          ┌──────────┐   ┌──────────┐  no hits   ┌─────────┐
 START ──▶│ analyze  │──▶│ retrieve │──────────▶ │ rewrite │──┐
          └──────────┘   └────┬─────┘ ◀──────────└─────────┘  │
                              │ hits                ▲──────────┘
                         ┌────▼─────┐   ┌──────────┐
                         │  expand  │──▶│ generate │──▶ END
                         └──────────┘   └──────────┘
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..index.store import CodeIndex, Hit
from .llm import LLMUnavailable, OllamaLLM
from .prompts import ANSWER_SYSTEM, PLANNER_SCHEMA, PLANNER_SYSTEM, REWRITE_SCHEMA, REWRITE_SYSTEM

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
ANSWER_TOKEN_RESERVE = 1800      # leave room in the context window for the answer
CHARS_PER_TOKEN = 3.5            # rough estimate for code

# Which edges to follow (and how far) for each intent
EXPANSION = {
    "explain": ({"CALLS", "DEFINES", "INHERITS"}, 2, 6),
    "locate": ({"CALLS", "DEFINES"}, 1, 3),
    "related": ({"CALLS", "DEFINES", "IMPORTS", "INHERITS"}, 1, 6),
    "repos": ({"DEFINES", "IMPORTS"}, 1, 4),
    "general": ({"CALLS", "DEFINES", "INHERITS"}, 1, 4),
}


class AgentState(TypedDict, total=False):
    question: str
    repo_filter: list[str]
    intent: str
    queries: list[str]
    symbols: list[str]
    attempts: int
    seeds: list[Hit]
    context: list[Hit]
    repo_summary: list[dict]
    answer: str
    sources: list[dict]
    trace: list[str]


# ---------------------------------------------------------------------------- rule-based planner
_IDENT_LIKE = re.compile(r"`([^`]+)`|\b([A-Za-z_]+[a-z][A-Z]\w*|[a-z]+_[a-z_]\w*|\w+\.\w+\(?\)?|\w+\(\))")


def heuristic_plan(question: str) -> dict:
    q = question.lower()
    if re.search(r"\bwhich (repo|repositories|projects?|services?)\b|\bwhat repo", q):
        intent = "repos"
    elif re.search(r"\bhow (does|do|is)\b|\bexplain\b|\bwalk me through\b|\bwhat does\b", q):
        intent = "explain"
    elif re.search(r"\brelated\b|\bwhich (files|functions|classes)\b|\binvolved\b", q):
        intent = "related"
    elif re.search(r"\bwhere\b|\bfind\b|\blocate\b|\bimplemented\b", q):
        intent = "locate"
    else:
        intent = "general"
    symbols = [a or b for a, b in _IDENT_LIKE.findall(question)]
    return {"intent": intent, "search_queries": [question], "symbols": [s.rstrip("()") for s in symbols], "repos": []}


# ---------------------------------------------------------------------------- agent
class CodeAssistant:
    def __init__(self, index: CodeIndex, llm: OllamaLLM | None, top_k: int = 6, max_context: int = 6,
                 max_snippet_lines: int = 40, use_llm_planner: bool = False, num_ctx: int = 8192):
        self.index = index
        self.llm = llm
        self.top_k = top_k
        self.max_context = max_context
        self.max_snippet_lines = max_snippet_lines
        self.use_llm_planner = use_llm_planner
        self.prompt_char_budget = int((num_ctx - ANSWER_TOKEN_RESERVE) * CHARS_PER_TOKEN)
        self.llm_note: str | None = None
        self._on_token: Callable[[str], None] | None = None
        self.app = self._build()

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("analyze", self.analyze)
        g.add_node("retrieve", self.retrieve)
        g.add_node("rewrite", self.rewrite)
        g.add_node("expand", self.expand)
        g.add_node("generate", self.generate)
        g.add_edge(START, "analyze")
        g.add_edge("analyze", "retrieve")
        g.add_conditional_edges("retrieve", self._route_after_retrieve, {"rewrite": "rewrite", "expand": "expand"})
        g.add_edge("rewrite", "retrieve")
        g.add_edge("expand", "generate")
        g.add_edge("generate", END)
        return g.compile()

    # -- helpers --------------------------------------------------------------
    def _llm_call(self, method: str, *args, **kwargs) -> Any:
        """Call the local LLM; on failure record a note and fall back (the next question retries)."""
        if not self.llm:
            return None
        try:
            return getattr(self.llm, method)(*args, **kwargs)
        except LLMUnavailable as e:
            log.warning("%s; continuing without the LLM", e)
            self.llm_note = str(e)
            return None

    # -- nodes ----------------------------------------------------------------
    def analyze(self, state: AgentState) -> dict:
        question = state["question"]
        plan = None
        if self.use_llm_planner:
            repo_list = ", ".join(self.index.repos) or "(none)"
            plan = self._llm_call("json", PLANNER_SYSTEM,
                                  f"Indexed repositories: {repo_list}\n\nQuestion: {question}", PLANNER_SCHEMA)
        plan = plan or heuristic_plan(question)
        queries = list(dict.fromkeys([question] + [q for q in plan.get("search_queries", []) if q.strip()]))[:5]
        repos = [r for r in (state.get("repo_filter") or plan.get("repos") or []) if r in self.index.repos]
        trace = [f"analyze: intent={plan['intent']} queries={queries} symbols={plan.get('symbols', [])}"
                 + (f" repos={repos}" if repos else "")]
        return {"intent": plan["intent"], "queries": queries, "symbols": plan.get("symbols", []),
                "repo_filter": repos, "attempts": 0, "trace": trace}

    def retrieve(self, state: AgentState) -> dict:
        k = self.top_k * (2 if state["intent"] == "repos" else 1)
        seeds = self.index.hybrid_search(state["queries"], k=k, repos=state.get("repo_filter") or None,
                                         symbols=state.get("symbols"))
        n = state.get("attempts", 0) + 1
        return {"seeds": seeds, "attempts": n, "trace": state["trace"] + [f"retrieve#{n}: {len(seeds)} hits"]}

    def _route_after_retrieve(self, state: AgentState) -> str:
        return "rewrite" if not state["seeds"] and state["attempts"] < MAX_ATTEMPTS else "expand"

    def rewrite(self, state: AgentState) -> dict:
        res = None
        if self.use_llm_planner:
            res = self._llm_call("json", REWRITE_SYSTEM,
                                 f"Question: {state['question']}\nQueries tried: {state['queries']}", REWRITE_SCHEMA)
        queries = (res or {}).get("search_queries") or re.findall(r"[A-Za-z_]{4,}", state["question"])
        return {"queries": queries, "trace": state["trace"] + [f"rewrite: {queries}"]}

    def expand(self, state: AgentState) -> dict:
        edge_types, hops, limit = EXPANSION.get(state["intent"], EXPANSION["general"])
        seeds = state["seeds"]
        neighbors = self.index.expand(seeds[: self.top_k], hops=hops, limit=limit, edge_types=edge_types)
        seeds = sorted(seeds, key=lambda h: -h.score)  # expand() boosts graph-connected seeds
        # keep the best seeds, but reserve slots for graph neighbours (half for flow/relationship questions)
        share = self.max_context // 2 if state["intent"] in ("explain", "related") else max(2, self.max_context // 3)
        n_seed = max(self.max_context - min(len(neighbors), share), 1)
        context = (seeds[:n_seed] + neighbors)[: self.max_context]
        summary = self.index.repo_summary(seeds) if state["intent"] == "repos" else []
        return {"context": context, "repo_summary": summary,
                "trace": state["trace"] + [f"expand: +{len(neighbors)} graph neighbours ({hops} hop), "
                                           f"{len(context)} chunks in context"]}

    def _format_source(self, n: int, hit: Hit) -> str:
        ch = self.index.chunks[hit.id]
        rels = []
        for direction, etype, other in self.index.relations(hit.id, limit=8):
            o = self.index.chunks.get(other)
            name = o.qualname if o else self.index.graph.nodes[other].get("name", other)
            rels.append(f"{'→' if direction == 'out' else '←'} {etype} {name}")
        lines = ch.code.splitlines()
        snippet = "\n".join(lines[: self.max_snippet_lines]) + ("\n…" if len(lines) > self.max_snippet_lines else "")
        return (f"[S{n}] {ch.kind} `{ch.qualname}` — {ch.repo}/{ch.path} lines {ch.start_line}-{ch.end_line}\n"
                + (f"Doc: {ch.docstring[:300]}\n" if ch.docstring and ch.kind != "file" else "")
                + (f"Relations: {'; '.join(rels)}\n" if rels else "")
                + f"```{ch.language}\n{snippet}\n```")

    def generate(self, state: AgentState) -> dict:
        context = state.get("context", [])
        if not context:
            return {"answer": "I couldn't find code related to that question in the indexed repositories.",
                    "sources": [], "trace": state["trace"] + ["generate: no context"]}

        # Build the prompt within the model's context budget
        header = f"Question: {state['question']}\n\n"
        if state.get("repo_summary"):
            header += "Repositories ranked by relevance:\n" + "\n".join(
                f"- {r['full_path']}: {', '.join(r['paths'][:5])}" for r in state["repo_summary"]) + "\n\n"
        blocks, used = [], len(header) + len(ANSWER_SYSTEM)
        for n, hit in enumerate(context, 1):
            block = self._format_source(n, hit)
            if blocks and used + len(block) > self.prompt_char_budget:
                break
            blocks.append(block)
            used += len(block)
        context = context[: len(blocks)]

        sources = []
        for n, hit in enumerate(context, 1):
            ch = self.index.chunks[hit.id]
            sources.append({"n": n, "id": hit.id, "repo": ch.repo, "path": ch.path, "qualname": ch.qualname,
                            "kind": ch.kind, "language": ch.language, "start_line": ch.start_line,
                            "end_line": ch.end_line, "url": self.index.citation_url(hit.id), "via": hit.via,
                            "reasons": hit.reasons, "score": round(hit.score, 4), "code": ch.code})

        user = header + "Sources:\n\n" + "\n\n".join(blocks) + "\n\nAnswer the question using the sources above."
        answer = self._llm_call("text", ANSWER_SYSTEM, user, on_token=self._on_token)
        if not answer:
            answer = self._extractive_answer(state, sources)
        answer = validate_citations(answer, len(sources))
        cited = {int(m) for m in re.findall(r"\[S(\d+)\]", answer)}
        for s in sources:
            s["cited"] = s["n"] in cited or not cited
        return {"answer": answer, "sources": sources,
                "trace": state["trace"] + [f"generate: {len(sources)} sources, ~{used // int(CHARS_PER_TOKEN)} prompt tokens"]}

    def _extractive_answer(self, state: AgentState, sources: list[dict]) -> str:
        """Retrieval-only answer used when the LLM is off or unreachable."""
        lines = ["*(Retrieval-only mode: these are the ranked matches.)*", ""]
        if state.get("repo_summary"):
            lines.append("**Repositories ranked by relevance:**")
            lines += [f"- **{r['full_path']}**: {', '.join(r['paths'][:4])}" for r in state["repo_summary"]]
            lines.append("")
        lines.append("**Most relevant code:**")
        for s in sources:
            tag = f" — {s['reasons'][0]}" if s["via"] == "graph" and s["reasons"] else ""
            lines.append(f"- [S{s['n']}] `{s['qualname']}` ({s['kind']}) in `{s['repo']}/{s['path']}` "
                         f"L{s['start_line']}-{s['end_line']}{tag}")
        return "\n".join(lines)

    # -- public ---------------------------------------------------------------
    def ask(self, question: str, repos: list[str] | None = None,
            on_token: Callable[[str], None] | None = None) -> AgentState:
        """Run the workflow. `on_token` receives the answer text as it streams from the model."""
        self.llm_note = None
        self._on_token = on_token
        try:
            result = self.app.invoke({"question": question, "repo_filter": repos or [], "trace": []})
        finally:
            self._on_token = None
        if self.llm_note:
            result["llm_note"] = self.llm_note
        return result


def validate_citations(answer: str, n_sources: int) -> str:
    """Small local models sometimes invent citation numbers; drop any [S#] that doesn't exist."""
    def fix(m: re.Match) -> str:
        return m.group(0) if 1 <= int(m.group(1)) <= n_sources else ""
    return re.sub(r"\[S(\d+)\]", fix, answer)


def format_sources(result: AgentState, only_cited: bool = True) -> str:
    out = []
    for s in result.get("sources", []):
        if only_cited and not s.get("cited"):
            continue
        loc = s["url"] or f"{s['repo']}/{s['path']}#L{s['start_line']}-{s['end_line']}"
        out.append(f"[S{s['n']}] {s['repo']}/{s['path']}:{s['start_line']}-{s['end_line']} ({s['qualname']})\n     {loc}")
    return "\n".join(out)
