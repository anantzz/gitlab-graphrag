from pathlib import Path

import pytest

from graphrag_gitlab.agent.graph import CodeAssistant, heuristic_plan
from graphrag_gitlab.config import Settings
from graphrag_gitlab.index.store import CodeIndex
from graphrag_gitlab.models import RepoInfo
from graphrag_gitlab.pipeline import build_index

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    settings = Settings()
    settings.data_dir = tmp_path_factory.mktemp("data")
    settings.embedding_backend = "hashing"
    repos = [
        RepoInfo(name=p.name, full_path=f"MS-ClientServices/{p.name}", local_path=str(p),
                 web_url=f"https://gitlab.example.com/MS-ClientServices/{p.name}", ref="abc123")
        for p in sorted(FIXTURES.iterdir()) if p.is_dir()
    ]
    build_index(settings, repos)
    return CodeIndex.load(settings.index_dir)  # also exercises save/load


def edges(index, etype):
    return {(index.graph.nodes[u].get("qualname"), index.graph.nodes[v].get("qualname"))
            for u, v, d in index.graph.edges(data=True) if d["type"] == etype}


def test_graph_structure(index):
    calls = edges(index, "CALLS")
    assert ("OrderService.place_order", "PaymentGateway.charge") in calls
    assert ("OrderService.place_order", "calculate_total") in calls
    assert ("OrderEventListener.onOrderPlaced", "EmailSender.send") in calls
    assert ("EmailSender.send", "EmailSender.renderTemplate") in calls
    assert ("EmailSender", "NotificationChannel") in edges(index, "INHERITS")
    imports = edges(index, "IMPORTS")
    assert ("orders/service.py", "orders/payments.py") in imports
    assert ("src/api/auth.ts", "src/api/http.ts") in imports
    assert "ext:sqlalchemy" in index.graph and "ext:axios" in index.graph
    assert "ext:orders" not in index.graph  # internal package must not look external


def test_hybrid_search_finds_symbols(index):
    hits = index.hybrid_search(["refund payment"], k=5)
    assert any(index.chunks[h.id].qualname == "PaymentGateway.refund" for h in hits)
    hits = index.hybrid_search(["token refresh"], k=5)
    assert index.chunks[hits[0].id].qualname == "refreshSession"


def test_citation_url(index):
    cid = index.lookup_symbols(["calculate_total"])[0]
    url = index.citation_url(cid)
    assert url.startswith("https://gitlab.example.com/MS-ClientServices/order-service/-/blob/abc123/orders/service.py#L")


def test_heuristic_intents():
    assert heuristic_plan("Where is login implemented?")["intent"] == "locate"
    assert heuristic_plan("How does place_order work?")["intent"] == "explain"
    assert heuristic_plan("How does place_order work?")["symbols"] == ["place_order"]
    assert heuristic_plan("Which repositories send email?")["intent"] == "repos"
    assert heuristic_plan("Which files are related to payments?")["intent"] == "related"


def test_agent_explain_expands_graph(index):
    assistant = CodeAssistant(index, llm=None)
    result = assistant.ask("How does place_order work?")
    names = [s["qualname"] for s in result["sources"]]
    assert names[0] == "OrderService.place_order"
    # graph expansion pulls in callees that plain search would not rank
    assert "PaymentGateway.charge" in names and "OrderRepository.save" in names
    assert all(s["url"] for s in result["sources"])


def test_agent_repos_intent(index):
    assistant = CodeAssistant(index, llm=None)
    result = assistant.ask("Which repositories contain code for sending email notifications?")
    assert result["repo_summary"][0]["repo"] == "notification-service"


def test_no_false_call_edges_for_builtin_methods(index):
    # `i.get("qty")` on a dict must not link to OrderRepository.get
    assert ("calculate_total", "OrderRepository.get") not in edges(index, "CALLS")
    assert ("OrderService.cancel_order", "OrderRepository.get") in edges(index, "CALLS")


class FakeLLM:
    """Stands in for OllamaLLM to exercise the LLM code path without a model."""

    def __init__(self):
        self.prompts = []

    def json(self, system, user, schema):
        self.prompts.append(user)
        return {"intent": "explain", "search_queries": ["place order charge payment"], "symbols": ["place_order"],
                "repos": []}

    def text(self, system, user, on_token=None):
        self.prompts.append(user)
        assert "[S1]" in user and "Relations:" in user
        answer = "`OrderService.place_order` charges via `PaymentGateway.charge` [S1][S2]. Also see [S99]."
        if on_token:
            for word in answer.split(" "):
                on_token(word + " ")
        return answer


def test_agent_with_llm_cites_sources(index):
    assistant = CodeAssistant(index, llm=FakeLLM(), use_llm_planner=True)
    streamed = []
    result = assistant.ask("How are orders placed?", on_token=streamed.append)
    assert result["intent"] == "explain"
    assert streamed, "answer should stream token by token"
    assert "[S99]" not in result["answer"], "invented citation numbers must be removed"
    cited = [s for s in result["sources"] if s["cited"]]
    assert [s["n"] for s in cited] == [1, 2]
    assert cited[0]["qualname"] == "OrderService.place_order"


def test_context_respects_budget(index):
    assistant = CodeAssistant(index, llm=None, max_context=6)
    result = assistant.ask("How does place_order work?")
    assert 1 <= len(result["sources"]) <= 6
