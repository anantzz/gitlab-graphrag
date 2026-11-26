"""GraphRAG assistant for GitLab repositories (local-only: Ollama + on-device embeddings)."""
from .privacy import apply_env_lockdown

apply_env_lockdown()

__version__ = "0.2.0"
