# GitLab GraphRAG Code Assistant (local only)

Ask questions about code in your GitLab repositories, such as "where is X implemented?", "how does Y work?" or "which
repos handle Z?". Answers include code snippets and links to the exact lines in GitLab.

Everything runs on your machine: an **Ollama** model (default `qwen2.5-coder:7b`), local `bge-small` embeddings,
a NetworkX knowledge graph and BM25 search. No code, question or answer is sent to any outside service.

See **IMPLEMENTATION_GUIDE.md** for the full setup, architecture and privacy verification steps.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt && pip install -e .
cp .env.example .env                                      # fill in GITLAB_URL / GITLAB_TOKEN / GITLAB_PROJECTS
ollama pull qwen2.5-coder:7b
graphrag-gitlab setup-models      # one-time embedding model download
graphrag-gitlab doctor            # checks Ollama, models, privacy settings
graphrag-gitlab ingest            # downloads the selected repos and builds the index
graphrag-gitlab ask "Where is authentication implemented?"
streamlit run app.py
```

Run the tests with `pytest -q`. They need neither Ollama nor network access.
