"""Local-only guard rails: disable third-party telemetry and refuse non-internal endpoints.

`apply_env_lockdown()` runs on package import (see __init__.py) so it takes effect before
langgraph / langsmith / huggingface libraries are imported.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

LOCKDOWN_ENV = {
    # LangChain / LangGraph tracing to LangSmith cloud
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING": "false",
    # HuggingFace / sentence-transformers telemetry
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    # Streamlit usage statistics
    "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
}


def apply_env_lockdown() -> None:
    for key, value in LOCKDOWN_ENV.items():
        os.environ[key] = value  # forced, not setdefault: a stray shell variable must not re-enable tracing
    for key in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"):
        os.environ.pop(key, None)
    # Offline model loading unless a one-time download was explicitly allowed
    if os.getenv("ALLOW_MODEL_DOWNLOAD", "false").lower() not in {"1", "true", "yes"}:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


class PrivacyError(SystemExit):
    pass


def _host_is_internal(host: str, extra_allowed: set[str]) -> bool:
    if host in extra_allowed or host == "localhost":
        return True
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    return bool(addrs) and all(
        ipaddress.ip_address(a.split("%")[0]).is_private or ipaddress.ip_address(a.split("%")[0]).is_loopback
        for a in addrs
    )


def check_endpoints(settings) -> None:
    """Refuse to run if the LLM endpoint is not loopback / private network, or the model runs in a cloud."""
    allowed = set(settings.allowed_hosts)
    if settings.use_llm and "cloud" in settings.ollama_model.lower():
        # Ollama "cloud" models (e.g. gpt-oss:120b-cloud) are proxied to ollama.com - code would leave the machine.
        raise PrivacyError(f"OLLAMA_MODEL={settings.ollama_model} is an Ollama cloud model that runs on remote "
                           f"servers. Use a local model such as qwen2.5-coder:7b.")
    if settings.use_llm:
        host = urlparse(settings.ollama_host).hostname or ""
        if not _host_is_internal(host, allowed):
            raise PrivacyError(
                f"OLLAMA_HOST_URL={settings.ollama_host} is not a local/private address. Refusing to send code there. "
                f"If this is an internal server, add its hostname to ALLOWED_HOSTS.")
    if settings.require_internal_gitlab:
        host = urlparse(settings.gitlab_url).hostname or ""
        if not _host_is_internal(host, allowed):
            raise PrivacyError(
                f"GITLAB_URL host {host} does not resolve to a private address. Add it to ALLOWED_HOSTS if it is "
                f"your company GitLab, or set REQUIRE_INTERNAL_GITLAB=false.")
