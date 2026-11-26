"""Exercise OllamaLLM against a fake Ollama HTTP server (no model or GPU needed)."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from graphrag_gitlab.agent.llm import LLMUnavailable, OllamaLLM
from graphrag_gitlab.config import Settings
from graphrag_gitlab.privacy import LOCKDOWN_ENV, check_endpoints

REQUESTS = []


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append(body)
        if body["model"] == "missing:latest":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "model 'missing:latest' not found"}).encode())
            return
        content = '{"search_queries": ["a", "b"]}' if body.get("format") else "Hello from the local model [S1]"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson" if body.get("stream") else "application/json")
        self.end_headers()
        if body.get("stream"):
            for word in content.split(" "):
                msg = {"model": body["model"], "message": {"role": "assistant", "content": word + " "}, "done": False}
                self.wfile.write((json.dumps(msg) + "\n").encode())
            self.wfile.write((json.dumps({"model": body["model"], "message": {"role": "assistant", "content": ""},
                                          "done": True}) + "\n").encode())
        else:
            self.wfile.write(json.dumps({"model": body["model"], "message": {"role": "assistant", "content": content},
                                         "done": True}).encode())


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_text_and_options(server):
    llm = OllamaLLM(server, "qwen2.5-coder:7b", num_ctx=8192)
    assert llm.text("sys", "hi") == "Hello from the local model [S1]"
    opts = REQUESTS[-1]["options"]
    assert opts["num_ctx"] == 8192, "num_ctx must be sent or Ollama truncates prompts"
    assert REQUESTS[-1]["messages"][0]["role"] == "system"


def test_streaming(server):
    llm = OllamaLLM(server, "qwen2.5-coder:7b")
    tokens = []
    out = llm.text("sys", "hi", on_token=tokens.append)
    assert len(tokens) > 1 and out.strip() == "Hello from the local model [S1]"


def test_json_schema(server):
    llm = OllamaLLM(server, "qwen2.5-coder:7b")
    assert llm.json("sys", "q", {"type": "object"}) == {"search_queries": ["a", "b"]}
    assert REQUESTS[-1]["format"] == {"type": "object"}


def test_missing_model(server):
    with pytest.raises(LLMUnavailable, match="ollama pull missing:latest"):
        OllamaLLM(server, "missing:latest").text("sys", "hi")


def test_ollama_not_running():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(LLMUnavailable, match="Cannot reach Ollama"):
        OllamaLLM(f"http://127.0.0.1:{port}", "x").text("sys", "hi")


def test_privacy_guard_rejects_public_llm_host():
    s = Settings()
    s.ollama_host = "http://8.8.8.8:11434"
    with pytest.raises(SystemExit, match="not a local/private address"):
        check_endpoints(s)
    s.ollama_host = "http://127.0.0.1:11434"
    check_endpoints(s)  # loopback is fine
    s.ollama_host = "http://10.1.2.3:11434"
    check_endpoints(s)  # private network is fine
    s.ollama_model = "gpt-oss:120b-cloud"
    with pytest.raises(SystemExit, match="cloud model"):
        check_endpoints(s)


def test_telemetry_locked_down():
    import os
    for k, v in LOCKDOWN_ENV.items():
        assert os.environ[k] == v
    assert os.environ.get("HF_HUB_OFFLINE") == "1"


def test_think_flag_only_sent_when_configured(server):
    OllamaLLM(server, "qwen2.5-coder:7b").text("sys", "hi")
    assert "think" not in REQUESTS[-1]
    OllamaLLM(server, "qwen3:4b", think=False).text("sys", "hi")
    assert REQUESTS[-1]["think"] is False
