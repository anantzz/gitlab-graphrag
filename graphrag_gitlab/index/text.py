"""Code-aware tokenization: splits camelCase / snake_case while keeping the full identifier."""
from __future__ import annotations

import re

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "is", "it", "and", "or", "for", "on", "with", "as", "by", "be", "this",
    "that", "are", "was", "where", "which", "what", "how", "does", "do", "i", "we", "our", "my", "me", "can",
    "from", "at", "into", "any", "all", "there", "their", "its", "work", "works", "implemented", "implement",
    "code", "file", "files", "function", "functions", "related", "particular", "repository", "repositories",
    "repo", "repos", "contain", "contains", "find", "show", "tell", "about", "please", "self", "return",
}


def _stem(word: str) -> str:
    for suf in ("ing", "ers", "er", "es", "ed", "s"):
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in _IDENT.findall(text):
        low = tok.lower()
        subs = [p.lower() for part in tok.split("_") for p in _CAMEL.findall(part)]
        if low not in STOPWORDS:
            out.append(low)
        if len(subs) > 1:
            out.extend(_stem(s) for s in subs if s not in STOPWORDS and len(s) > 1)
        elif low not in STOPWORDS:
            stem = _stem(low)
            if stem != low:
                out.append(stem)
    return out
