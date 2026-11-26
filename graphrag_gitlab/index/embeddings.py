"""Embedding backends for the vector half of hybrid search."""
from __future__ import annotations

import hashlib
import logging

import numpy as np

from .text import tokenize

log = logging.getLogger(__name__)


class HashingEmbedder:
    """Dependency-free feature-hashing embedder (lexical, not semantic). Useful offline and in tests."""

    name = "hashing"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        feats = toks + [t[i:i + 3] for t in toks for i in range(max(len(t) - 2, 0))]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


class SentenceTransformerEmbedder:
    name = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        # BGE models expect an instruction prefix on queries
        self.query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in model_name.lower() else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=len(texts) > 200),
                          dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(self.model.encode([self.query_prefix + text], normalize_embeddings=True)[0], dtype=np.float32)


def make_embedder(backend: str, model_name: str):
    if backend == "sentence-transformers":
        try:
            return SentenceTransformerEmbedder(model_name)
        except ImportError:
            log.warning("sentence-transformers not installed; falling back to hashing embedder. "
                        "Run `pip install sentence-transformers` for semantic search.")
    return HashingEmbedder()
