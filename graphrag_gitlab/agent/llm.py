"""Local LLM via Ollama. Prompts and code never leave the machine (or your internal network)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import ollama

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


class OllamaLLM:
    def __init__(self, host: str, model: str, num_ctx: int = 8192, temperature: float = 0.1,
                 keep_alive: str = "30m", timeout: float = 600.0, think: bool | None = None):
        self.host = host
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.think = think
        self.client = ollama.Client(host=host, timeout=timeout)

    def _options(self, num_predict: int) -> dict:
        # num_ctx MUST be set explicitly: Ollama's default context is small and silently truncates long prompts.
        return {"num_ctx": self.num_ctx, "temperature": self.temperature, "num_predict": num_predict}

    def _chat(self, system: str, user: str, *, fmt: dict | None, num_predict: int,
              on_token: Callable[[str], None] | None = None) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        extra = {} if self.think is None else {"think": self.think}  # only for thinking-capable models
        try:
            if on_token:
                parts = []
                for chunk in self.client.chat(model=self.model, messages=messages, format=fmt, stream=True,
                                              options=self._options(num_predict), keep_alive=self.keep_alive, **extra):
                    piece = chunk.message.content or ""
                    if piece:
                        parts.append(piece)
                        on_token(piece)
                return "".join(parts)
            resp = self.client.chat(model=self.model, messages=messages, format=fmt, stream=False,
                                    options=self._options(num_predict), keep_alive=self.keep_alive, **extra)
            return resp.message.content or ""
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise LLMUnavailable(f"Model '{self.model}' is not pulled. Run: ollama pull {self.model}") from e
            raise LLMUnavailable(f"Ollama error {e.status_code}: {e.error}") from e
        except (ConnectionError, httpx.HTTPError) as e:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.host}. Is it running? ({e})") from e

    def json(self, system: str, user: str, schema: dict) -> dict:
        text = self._chat(system, user, fmt=schema, num_predict=512)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMUnavailable(f"Model returned invalid JSON: {e}") from e

    def text(self, system: str, user: str, on_token: Callable[[str], None] | None = None) -> str:
        return self._chat(system, user, fmt=None, num_predict=1500, on_token=on_token)


def make_llm(settings) -> OllamaLLM | None:
    if not settings.use_llm:
        return None
    return OllamaLLM(settings.ollama_host, settings.ollama_model, num_ctx=settings.num_ctx,
                     temperature=settings.temperature, keep_alive=settings.ollama_keep_alive,
                     think=settings.ollama_think)
