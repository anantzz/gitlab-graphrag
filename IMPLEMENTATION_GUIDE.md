# GitLab GraphRAG Code Assistant: Implementation Guide (Local-Only, Ollama)

> **Version:** 0.2.0 · **Last verified:** 2026-11-27. All 17 automated tests pass against the exact code in this guide.
> **Target machine:** Lenovo ThinkPad, AMD Ryzen 5, 16 GB RAM, CPU-only, Windows 10/11 (Linux commands also given).

This guide is self-contained. It has every install step, every dependency, the full source of every file,
the tests, and how to prove that no data leaves the machine. You can follow it by hand, hand it to GitHub
Copilot phase by phase, or extract every file automatically with the script in [Appendix A](#appendix-a-extract-all-files-automatically).

---

## Table of contents

0. [Instructions for GitHub Copilot](#0-instructions-for-github-copilot)
1. [What we are building](#1-what-we-are-building)
2. [Privacy rules (non-negotiable)](#2-privacy-rules-non-negotiable)
3. [Architecture](#3-architecture)
4. [Prerequisites and hardware expectations](#4-prerequisites-and-hardware-expectations)
5. [Phase 1: Install system software](#5-phase-1-install-system-software)
6. [Phase 2: Install and lock down Ollama, pull the model](#6-phase-2-install-and-lock-down-ollama-pull-the-model)
7. [Phase 3: Project skeleton and Python dependencies](#7-phase-3-project-skeleton-and-python-dependencies)
8. [Phase 4: Configuration and privacy guard](#8-phase-4-configuration-and-privacy-guard)
9. [Phase 5: Data model](#9-phase-5-data-model)
10. [Phase 6: Ingestion (GitLab and local)](#10-phase-6-ingestion-gitlab-and-local)
11. [Phase 7: Code parsing](#11-phase-7-code-parsing)
12. [Phase 8: Knowledge graph](#12-phase-8-knowledge-graph)
13. [Phase 9: Hybrid search index](#13-phase-9-hybrid-search-index)
14. [Phase 10: Local LLM client and prompts](#14-phase-10-local-llm-client-and-prompts)
15. [Phase 11: LangGraph agent](#15-phase-11-langgraph-agent)
16. [Phase 12: Pipeline, CLI and web UI](#16-phase-12-pipeline-cli-and-web-ui)
17. [Phase 13: Tests and sample repositories](#17-phase-13-tests-and-sample-repositories)
18. [Phase 14: First run](#18-phase-14-first-run)
19. [Phase 15: Index the real MS-ClientServices repositories](#19-phase-15-index-the-real-ms-clientservices-repositories)
20. [Phase 16: Prove that nothing leaves the machine](#20-phase-16-prove-that-nothing-leaves-the-machine)
21. [Performance tuning for a CPU-only laptop](#21-performance-tuning-for-a-cpu-only-laptop)
22. [Troubleshooting](#22-troubleshooting)
23. [Next steps after the prototype](#23-next-steps-after-the-prototype)
- [Appendix A: Extract all files automatically](#appendix-a-extract-all-files-automatically)
- [Appendix B: Tested versions](#appendix-b-tested-versions)

---

## 0. Instructions for GitHub Copilot

If you are using Copilot (Chat or Agent mode) to implement this, paste this block into the chat first:

```text
You are implementing a local-only code assistant from IMPLEMENTATION_GUIDE.md.
Rules:
1. Work phase by phase, in order. Do not skip ahead.
2. Create every file with EXACTLY the content shown under "File: `<path>`". Do not rewrite, "improve",
   rename or reformat the code unless a checkpoint fails.
3. After each phase, run that phase's checkpoint command and show me the output. If it fails, fix only
   what the error points to, then rerun it.
4. NEVER add cloud or telemetry dependencies or calls: no openai, anthropic, langchain-openai, langsmith,
   google-generativeai, cohere, mistralai, sentry, posthog, or any HTTP call to a non-local endpoint.
5. NEVER change the behaviour of graphrag_gitlab/privacy.py or remove calls to check_endpoints().
6. NEVER put real tokens, passwords or company code into files that get committed (.env is git-ignored).
7. Use the Windows PowerShell commands unless I say I am on Linux.
```

Then, for each phase, ask: *"Implement Phase N from IMPLEMENTATION_GUIDE.md and run its checkpoint."*

Copilot itself is a cloud service. Under the privacy rules, don't paste real company code into Copilot Chat or
open the downloaded repositories in an editor where Copilot is enabled, unless your company has approved
Copilot for that code. Building this tool only involves the code in this guide, which contains no company data.
Indexed repositories land in `data/`. Exclude that folder from Copilot (see Phase 3).

---

## 1. What we are building

A developer assistant that answers natural-language questions about source code in a **small set of sample
GitLab repositories from the `MS-ClientServices` group**, with code snippets and **citations that link to the
exact file and lines in GitLab**.

Example questions it answers:

| Question type | Example | What the system does |
|---|---|---|
| Locate | "Where is token refresh implemented?" | Hybrid search, then 1 hop in the graph |
| Related | "Which files are related to payment processing?" | Search, then neighbours via calls, imports, definitions and inheritance |
| Explain | "How does `place_order` work?" | Search, then follows the call graph 2 hops so the answer covers the whole flow |
| Repositories | "Which repositories send email notifications?" | Search, then ranks repositories by aggregated relevance |

**Key properties**

- Runs entirely on the laptop. The LLM is **Ollama + `qwen2.5-coder:7b`** (Apache 2.0, free for commercial use).
- **GraphRAG**: a knowledge graph of repos, files, classes and functions, connected by `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`
  and `INHERITS` edges, combined with **hybrid BM25 + vector search** and orchestrated by **LangGraph**.
- Works **without** an LLM too (retrieval-only mode). It still returns ranked code locations and links.
- Small and inspectable: about 2,000 lines of Python with no database server. The graph and vectors are files in `data/`.

---

## 2. Privacy rules (non-negotiable)

| # | Rule | How the code enforces it |
|---|---|---|
| P1 | The LLM runs locally (or on an approved internal server) | `privacy.check_endpoints()` refuses any `OLLAMA_HOST_URL` that isn't loopback or a private IP, unless it's listed in `ALLOWED_HOSTS` |
| P2 | No Ollama *cloud* models | `check_endpoints()` refuses model names containing `cloud` (these are proxied to ollama.com) |
| P3 | No telemetry or tracing | `privacy.apply_env_lockdown()` runs on package import. It forces LangSmith tracing off, HuggingFace telemetry off and Streamlit usage stats off, and removes LangSmith API keys |
| P4 | Embedding model loads offline | `HF_HUB_OFFLINE=1` is forced. The model is downloaded once with `graphrag-gitlab setup-models` |
| P5 | The web UI is not reachable from the network | `.streamlit/config.toml` binds it to `127.0.0.1` |
| P6 | Ollama is not reachable from the network | Leave `OLLAMA_HOST` (Ollama's own server variable) **unset**, so it binds to `127.0.0.1:11434` |
| P7 | GitLab access is read-only and minimal | Token with only the `read_api` and `read_repository` scopes |
| P8 | Indexed code is protected at rest | Full-disk encryption (BitLocker); `data/` is git-ignored |
| P9 | Provable | Phase 16 blocks all outbound traffic with the firewall and shows the assistant still works |

The only outbound connections ever made:
1. **One-time setup:** downloading Python packages (PyPI), the Ollama installer and model (ollama.com), and the
   embedding model (huggingface.co). These are downloads only; no company data is sent.
2. **Ingestion:** `python-gitlab` talking to **your company GitLab** to download the selected repositories.

Asking questions makes **no** outbound connections.

---

## 3. Architecture

```
                       ┌──────────────── ingestion (graphrag-gitlab ingest) ────────────────┐
 Company GitLab ──────▶│ gitlab_source: list group projects → download zip @ pinned commit  │
 (MS-ClientServices)   │ parsing: Python AST / regex+brace parser / Markdown sections        │
                       │ graph.builder: NetworkX MultiDiGraph                               │
                       │ index.store: BM25 + bge-small vectors  →  data/index/*.json|npy     │
                       └────────────────────────────────────────────────────────────────────┘

                       ┌──────────────── question time (ask / chat / web UI) ───────────────┐
 Developer question ──▶│ LangGraph:  analyze → retrieve ⇄ rewrite → expand → generate        │──▶ Answer + [S#]
                       │              (rules)   (hybrid)   (retry)   (graph)  (Ollama, local) │    citations with
                       └────────────────────────────────────────────────────────────────────┘    GitLab line links
```

### 3.1 Knowledge-graph schema

| Node id pattern | Kind | Example |
|---|---|---|
| `repo:<repo>` | repository | `repo:order-service` |
| `file:<repo>:<path>` | file (also a searchable "file summary" chunk) | `file:order-service:orders/service.py` |
| `sym:<repo>:<path>:<qualname>:<line>` | class / function / method / doc section | `sym:order-service:orders/service.py:OrderService.place_order:12` |
| `ext:<package>` | third-party dependency | `ext:sqlalchemy`, `ext:axios` |

| Edge | Meaning |
|---|---|
| `CONTAINS` | repo → file |
| `DEFINES` | file → top-level symbol, class → method, file → doc section |
| `IMPORTS` | file → file (resolved inside the repo) or file → `ext:` dependency |
| `CALLS` | function/method → function/method. Python infers receiver types (`self.repo = OrderRepository()` makes `self.repo.save()` resolve to `OrderRepository.save`). Common container methods (`dict.get`, `list.append`) are filtered out to avoid false edges |
| `INHERITS` | class → base class / interface |

### 3.2 Retrieval and ranking

1. **BM25** over a code-aware tokenizer that splits `camelCase` and `snake_case` and keeps the full identifier.
2. **Vector search** with `BAAI/bge-small-en-v1.5` (about 130 MB, runs on CPU), as cosine similarity over a NumPy matrix.
3. **Reciprocal-rank fusion** of both, over several query variants, plus a boost for exact symbol names.
4. **Graph expansion.** Relevance spreads along edges, weighted by edge type and direction. Search hits
   connected to a higher-ranked hit are boosted ("graph-confirmed").
5. **Context budget.** About 6 chunks of at most 40 lines each, sized to fit `NUM_CTX` with room left for the answer.

### 3.3 Agent workflow (LangGraph `StateGraph`)

| Node | What it does |
|---|---|
| `analyze` | Detects intent (`locate`, `related`, `explain`, `repos` or `general`) and identifiers. Rule-based by default, which is instant on CPU; `USE_LLM_PLANNER=true` has the LLM do it |
| `retrieve` | Hybrid search |
| `rewrite` | Runs only when nothing relevant was found: reformulates the queries and retries once |
| `expand` | Graph traversal. Edge types and depth depend on intent |
| `generate` | Builds numbered sources and streams the answer from Ollama. Removes citation numbers that don't exist. Without an LLM, it returns a ranked list instead |

### 3.4 Final project layout

```
gitlab-graphrag/
├── .env.example            # configuration template (copy to .env)
├── .gitignore
├── .streamlit/config.toml  # UI bound to localhost, no usage stats
├── README.md
├── IMPLEMENTATION_GUIDE.md # this file
├── pyproject.toml
├── requirements.txt
├── app.py                  # Streamlit web UI
├── graphrag_gitlab/
│   ├── __init__.py         # applies the privacy lockdown on import
│   ├── privacy.py          # telemetry lockdown + endpoint guard
│   ├── config.py           # settings from environment / .env
│   ├── models.py           # RepoInfo, Symbol, FileInfo
│   ├── pipeline.py         # ingest → parse → graph → index → save
│   ├── cli.py              # graphrag-gitlab command
│   ├── ingest/  (gitlab_source.py, local_source.py)
│   ├── parsing/ (__init__.py, python_parser.py, generic_parser.py)
│   ├── graph/   (builder.py)
│   ├── index/   (text.py, embeddings.py, store.py)
│   └── agent/   (llm.py, prompts.py, graph.py)
├── tests/
│   ├── test_pipeline.py
│   ├── test_local_llm.py
│   └── fixtures/  (3 tiny sample repos: Python, Java, TypeScript)
└── data/                   # created at runtime: downloaded repos + index (git-ignored)
```

---

## 4. Prerequisites and hardware expectations

| Item | Requirement |
|---|---|
| OS | Windows 10 22H2+ or Windows 11 (or Ubuntu 22.04+) |
| CPU / RAM | Ryzen 5, 16 GB. Expect about 9–11 GB free for the model after Windows and a browser |
| Disk | About 15 GB free: Ollama (1 GB), qwen2.5-coder:7b (4.7 GB), Python env with CPU torch (about 2 GB), repos + index |
| Network | Needed only for setup and ingestion. Company proxy or pip mirror if required |
| Accounts | GitLab access to the MS-ClientServices sample repos (read-only token) |
| Admin rights | Needed to install software and to add firewall rules in Phase 16 |

**Expected speed on this laptop (CPU-only, estimates):**

| Model | RAM | Time to first word | Total answer time |
|---|---|---|---|
| `qwen2.5-coder:7b` (default) | ~6–8 GB | 15–60 s | 30–90 s |
| `qwen3:4b` with `OLLAMA_THINK=false` | ~4–5 GB | 8–30 s | 15–40 s |
| Retrieval only (`--no-llm`) | ~1 GB | < 2 s | < 2 s |

The first question after start-up is slower because the model loads into RAM. Later ones reuse it
for `OLLAMA_KEEP_ALIVE` (30 minutes).

---

## 5. Phase 1: Install system software

### 5.1 Windows (PowerShell)

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
# Close and reopen PowerShell so PATH updates, then verify:
git --version
py -3.12 --version
```

If `winget` is blocked by company policy, use the installers from git-scm.com and python.org. During the Python
install, tick **"Add python.exe to PATH"**.

### 5.2 Linux (Ubuntu)

```bash
sudo apt update
sudo apt install -y git python3.12 python3.12-venv
python3.12 --version
```

**Checkpoint 1:** `git --version` and Python 3.12 (3.11–3.13 also work) print versions.

---

## 6. Phase 2: Install and lock down Ollama, pull the model

### 6.1 Install

**Windows:**
```powershell
winget install --id Ollama.Ollama -e
# or download OllamaSetup.exe from https://ollama.com/download/windows
```
Ollama starts automatically and shows a llama icon in the system tray.

**Linux:** download the install script, read it, then run it:
```bash
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh
less ollama-install.sh
sh ollama-install.sh
```

### 6.2 Configure Ollama for a 16 GB CPU-only laptop

**Windows (PowerShell, current user):**
```powershell
setx OLLAMA_KEEP_ALIVE 30m          # keep model in RAM between questions
setx OLLAMA_NUM_PARALLEL 1          # one request at a time (saves RAM)
setx OLLAMA_MAX_LOADED_MODELS 1     # never hold two models in RAM
# Do NOT set OLLAMA_HOST. Unset means Ollama listens on 127.0.0.1 only.
```
Right-click the tray icon, choose **Quit Ollama**, then start Ollama again from the Start menu so it picks up the variables.

**Linux (systemd):**
```bash
sudo systemctl edit ollama
# add:
# [Service]
# Environment="OLLAMA_KEEP_ALIVE=30m"
# Environment="OLLAMA_NUM_PARALLEL=1"
# Environment="OLLAMA_MAX_LOADED_MODELS=1"
sudo systemctl restart ollama
```

### 6.3 Privacy settings for Ollama

- **Don't sign in** to an ollama.com account from Ollama, and **never** pull or run models whose names end in
  `-cloud`. Those run on remote servers. The app refuses them as well.
- Keep Ollama bound to localhost (rule P6). Verify it:
  ```powershell
  netstat -ano | findstr :11434      # must show 127.0.0.1:11434 LISTENING, NOT 0.0.0.0
  ```
  ```bash
  ss -ltnp | grep 11434              # Linux: 127.0.0.1:11434
  ```

### 6.4 Pull the model (one-time, about 4.7 GB)

```powershell
ollama pull qwen2.5-coder:7b
# optional faster alternative:
ollama pull qwen3:4b
ollama list
```

Licences: `qwen2.5-coder:7b`, `qwen3:4b` and `phi4-mini` are Apache 2.0 / MIT and allow commercial use.
**Do not** use `qwen2.5-coder:3b` (research licence, no commercial use) or Codestral (non-production licence).
Have legal confirm the licence of whichever model you choose.

### 6.5 Smoke test

```powershell
ollama run qwen2.5-coder:7b "Write a one-line Python function that reverses a string."
```

**Checkpoint 2:** the model answers, and `netstat` shows Ollama on `127.0.0.1:11434` only.

---

## 7. Phase 3: Project skeleton and Python dependencies

### 7.1 Create the folders

**Windows:**
```powershell
mkdir C:\dev\gitlab-graphrag; cd C:\dev\gitlab-graphrag
mkdir graphrag_gitlab\ingest, graphrag_gitlab\parsing, graphrag_gitlab\graph, graphrag_gitlab\index, graphrag_gitlab\agent, tests\fixtures, .streamlit
```
**Linux:**
```bash
mkdir -p ~/dev/gitlab-graphrag && cd ~/dev/gitlab-graphrag
mkdir -p graphrag_gitlab/{ingest,parsing,graph,index,agent} tests/fixtures .streamlit
```

### 7.2 Packaging and dependency files

**File: `pyproject.toml`**

````toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "graphrag-gitlab"
version = "0.2.0"
description = "GraphRAG assistant for GitLab repositories: code knowledge graph + hybrid BM25/vector search + LangGraph + local Ollama LLM"
requires-python = ">=3.10"
dependencies = [
    "ollama>=0.6",
    "httpx>=0.27",
    "langgraph>=1.0",
    "networkx>=3.2",
    "numpy>=1.26",
    "rank-bm25>=0.2.2",
    "python-gitlab>=4.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
semantic = ["sentence-transformers>=3.0"]
ui = ["streamlit>=1.35"]
dev = ["pytest>=8"]

[project.scripts]
graphrag-gitlab = "graphrag_gitlab.cli:main"

[tool.setuptools.packages.find]
include = ["graphrag_gitlab*"]
````

**File: `requirements.txt`**

````text
# Core (tested versions in comments)
langgraph>=1.0          # 1.2.24
networkx>=3.2           # 3.7
numpy>=1.26             # 2.5.5
rank-bm25>=0.2.2        # 0.2.2
python-gitlab>=4.0      # 8.6.0
python-dotenv>=1.0      # 1.2.4
ollama>=0.6             # 0.6.3
httpx>=0.27             # 0.29.0
# Local semantic embeddings (install CPU-only torch first, see guide)
sentence-transformers>=3.0   # 6.3.0
# UI and tests
streamlit>=1.35         # 1.67.0
pytest>=8               # 9.2.0
````

**File: `.gitignore`**

````gitignore
.venv/
data/
.env
__pycache__/
*.egg-info/
.pytest_cache/
.DS_Store
````

**File: `.streamlit/config.toml`**

````toml
[browser]
gatherUsageStats = false

[server]
address = "127.0.0.1"
headless = true
````

**File: `README.md`**

````markdown
# GitLab GraphRAG Code Assistant (local only)

Ask questions about code in your GitLab repositories, such as "where is X implemented?", "how does Y work?" or "which
repos handle Z?". Answers include code snippets and links to the exact lines in GitLab.

Everything runs on your machine: an **Ollama** model (default `qwen2.5-coder:7b`), local `bge-small` embeddings,
a NetworkX knowledge graph and BM25 search. No code, question or answer is sent to any outside service.

- **New to AI?** Start with **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**. It explains everything from scratch
  in plain language: LLMs, RAG, search, knowledge graphs, and a step-by-step walk-through of one question.
- **Setting it up?** See **[IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)** for the full install, architecture and
  privacy verification steps.

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
````

**Dependency overview** (all open source, all run locally):

| Package | Licence | Why |
|---|---|---|
| `langgraph` | MIT | Agent workflow (state graph) |
| `networkx` | BSD | Knowledge graph |
| `numpy` | BSD | Vector matrix + cosine similarity |
| `rank-bm25` | Apache 2.0 | BM25 keyword search |
| `python-gitlab` | LGPL-3.0 | GitLab API client (used unmodified as a library) |
| `python-dotenv` | BSD | Read `.env` |
| `ollama` | MIT | Client for the local Ollama server |
| `httpx` | BSD | HTTP client used by `ollama` (we catch its errors) |
| `sentence-transformers` (+ `torch` CPU) | Apache 2.0 / BSD | Local embedding model |
| `streamlit` | Apache 2.0 | Local web UI |
| `pytest` | MIT | Tests |

### 7.3 Virtual environment and install

**Windows:**
```powershell
cd C:\dev\gitlab-graphrag
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (or use .venv\Scripts\activate.bat in cmd)
python -m pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu    # CPU-only torch (small; no CUDA)
pip install -r requirements.txt
```
**Linux:**
```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu    # avoids a ~3 GB CUDA download
pip install -r requirements.txt
```

Behind a company proxy or mirror: `pip config set global.index-url https://<your-internal-pypi>/simple`
or add `--proxy http://proxy:port`.

### 7.4 Keep Copilot away from downloaded code

Add this to `.vscode/settings.json` so Copilot never reads indexed company code:
```json
{
  "github.copilot.enable": { "*": true },
  "files.exclude": { "data/**": true },
  "search.exclude": { "data/**": true }
}
```
Also add `data/` as a content exclusion in your organisation's Copilot settings, if your admin supports it.

**Checkpoint 3:** `python -c "import langgraph, networkx, rank_bm25, gitlab, ollama, sentence_transformers; print('ok')"` prints `ok`.

---

## 8. Phase 4: Configuration and privacy guard

**What and why.** All settings come from environment variables or `.env`. `privacy.py` is imported first, through
the package `__init__`, so telemetry is disabled **before** LangGraph, LangSmith or HuggingFace are imported.
`check_endpoints()` is called by the CLI and the UI before any work starts.

**File: `graphrag_gitlab/__init__.py`**

````python
"""GraphRAG assistant for GitLab repositories (local-only: Ollama + on-device embeddings)."""
from .privacy import apply_env_lockdown

apply_env_lockdown()

__version__ = "0.2.0"
````

**File: `graphrag_gitlab/privacy.py`**

````python
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
````

**File: `graphrag_gitlab/config.py`**

````python
"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_list(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- GitLab -----------------------------------------------------------
    gitlab_url: str = field(default_factory=lambda: _env("GITLAB_URL", ""))
    gitlab_token: str | None = field(default_factory=lambda: _env("GITLAB_TOKEN"))
    gitlab_group: str = field(default_factory=lambda: _env("GITLAB_GROUP", "MS-ClientServices"))
    # Explicit project list (path_with_namespace or project name). Empty = pick from the group.
    gitlab_projects: list[str] = field(default_factory=lambda: _env_list("GITLAB_PROJECTS"))
    gitlab_max_projects: int = field(default_factory=lambda: int(_env("GITLAB_MAX_PROJECTS", "5")))
    gitlab_ref: str | None = field(default_factory=lambda: _env("GITLAB_REF"))  # default branch if unset
    gitlab_verify_ssl: bool = field(default_factory=lambda: _env_bool("GITLAB_VERIFY_SSL", True))

    # --- Storage ----------------------------------------------------------
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())

    # --- Parsing limits ---------------------------------------------------
    max_file_bytes: int = field(default_factory=lambda: int(_env("MAX_FILE_BYTES", "400000")))

    # --- Retrieval --------------------------------------------------------
    # "sentence-transformers" (semantic, runs locally on CPU) or "hashing" (no ML deps, lexical only)
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "sentence-transformers"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "6")))
    # Kept small for CPU-only laptops: every extra snippet adds seconds of prompt processing.
    max_context_chunks: int = field(default_factory=lambda: int(_env("MAX_CONTEXT_CHUNKS", "6")))
    max_snippet_lines: int = field(default_factory=lambda: int(_env("MAX_SNIPPET_LINES", "40")))

    # --- Local LLM (Ollama) -----------------------------------------------
    use_llm: bool = field(default_factory=lambda: _env_bool("USE_LLM", True))
    ollama_host: str = field(default_factory=lambda: _env("OLLAMA_HOST_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    num_ctx: int = field(default_factory=lambda: int(_env("NUM_CTX", "8192")))
    temperature: float = field(default_factory=lambda: float(_env("TEMPERATURE", "0.1")))
    ollama_keep_alive: str = field(default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "30m"))
    # Thinking models (qwen3, gpt-oss): "false" makes them answer directly (much faster on CPU).
    # Leave empty for non-thinking models such as qwen2.5-coder.
    ollama_think: bool | None = field(default_factory=lambda: None if _env("OLLAMA_THINK") is None
                                      else _env_bool("OLLAMA_THINK", False))
    # Use the LLM to plan queries (better recall, one extra LLM call). Off by default on CPU: rule-based planner.
    use_llm_planner: bool = field(default_factory=lambda: _env_bool("USE_LLM_PLANNER", False))

    # --- Privacy ----------------------------------------------------------
    # Extra hostnames treated as internal (e.g. an internal GPU server running Ollama)
    allowed_hosts: list[str] = field(default_factory=lambda: _env_list("ALLOWED_HOSTS"))
    require_internal_gitlab: bool = field(default_factory=lambda: _env_bool("REQUIRE_INTERNAL_GITLAB", False))

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"
````

**File: `.env.example`**

````dotenv
# ---------------- GitLab (your company instance) ----------------
GITLAB_URL=https://gitlab.your-company.internal
# Personal access token with ONLY read_api + read_repository scopes
GITLAB_TOKEN=
GITLAB_GROUP=MS-ClientServices
# Sample repos to index (comma-separated project names or full paths).
# Leave empty to take the GITLAB_MAX_PROJECTS most recently active projects in the group.
GITLAB_PROJECTS=
GITLAB_MAX_PROJECTS=5
# GITLAB_REF=main
# Set to false only if your company GitLab uses a self-signed certificate
GITLAB_VERIFY_SSL=true

# ---------------- Local LLM (Ollama) ----------------
OLLAMA_HOST_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:7b
# Context window sent to Ollama on every call (Ollama's default is too small for RAG)
NUM_CTX=8192
TEMPERATURE=0.1
OLLAMA_KEEP_ALIVE=30m
# Only for thinking models (qwen3, gpt-oss): false = answer directly, much faster on CPU
# OLLAMA_THINK=false
# Let the LLM rewrite search queries (better recall, but one extra slow call on CPU)
USE_LLM_PLANNER=false
USE_LLM=true

# ---------------- Retrieval ----------------
EMBEDDING_BACKEND=sentence-transformers
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
TOP_K=6
MAX_CONTEXT_CHUNKS=6
MAX_SNIPPET_LINES=40
DATA_DIR=./data

# ---------------- Privacy ----------------
# Extra hostnames treated as internal (e.g. an internal GPU server running Ollama)
ALLOWED_HOSTS=
# true = refuse to run unless GITLAB_URL resolves to a private IP
REQUIRE_INTERNAL_GITLAB=false
````

Create your own `.env` from the template now (Windows: `copy .env.example .env`, Linux: `cp .env.example .env`).
You'll fill in the GitLab values in Phase 15.

**Checkpoint 4:** `python -c "import graphrag_gitlab, os; print(os.environ['LANGSMITH_TRACING'], os.environ['HF_HUB_OFFLINE'])"` prints `false 1`.

---

## 9. Phase 5: Data model

**What and why.** `Symbol` is the unit of retrieval: a function, method, class, file summary or doc section. Its `id`
is also its graph node id. `RepoInfo.blob_url()` builds GitLab permalinks of the form
`<web_url>/-/blob/<commit sha>/<path>#L<start>-<end>`.

**File: `graphrag_gitlab/models.py`**

````python
"""Core data structures shared by ingestion, parsing, graph and retrieval."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import quote


@dataclass
class RepoInfo:
    name: str                      # short name, e.g. "order-service"
    full_path: str                 # path_with_namespace, e.g. "MS-ClientServices/order-service"
    local_path: str                # where the source lives on disk
    web_url: str | None = None     # e.g. https://gitlab.example.com/MS-ClientServices/order-service
    ref: str | None = None         # commit sha (preferred, permalinks) or branch
    description: str = ""

    def blob_url(self, path: str, start: int | None = None, end: int | None = None) -> str | None:
        if not self.web_url:
            return None
        url = f"{self.web_url.rstrip('/')}/-/blob/{self.ref or 'HEAD'}/{quote(path)}"
        if start:
            url += f"#L{start}" + (f"-{end}" if end and end != start else "")
        return url

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Symbol:
    """A retrievable unit of code: function, method, class, file summary or doc section."""

    id: str
    repo: str
    path: str
    name: str
    qualname: str
    kind: str                      # function | method | class | file | doc
    language: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    code: str = ""
    parent: str | None = None      # id of the enclosing class / file
    calls: list[str] = field(default_factory=list)   # raw callee names
    bases: list[str] = field(default_factory=list)   # raw base-class names

    def header(self) -> str:
        return f"{self.repo}/{self.path} :: {self.qualname} ({self.kind}, {self.language})"

    def index_text(self, max_chars: int = 2000) -> str:
        parts = [self.header()]
        if self.signature:
            parts.append(self.signature)
        if self.docstring:
            parts.append(self.docstring)
        parts.append(self.code[:max_chars])
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileInfo:
    repo: str
    path: str
    language: str
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)


def file_id(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def symbol_id(repo: str, path: str, qualname: str, line: int) -> str:
    return f"sym:{repo}:{path}:{qualname}:{line}"


def repo_id(repo: str) -> str:
    return f"repo:{repo}"
````

---

## 10. Phase 6: Ingestion (GitLab and local)

**What and why.**
- `gitlab_source.py` lists projects in the group, including subgroups. It takes either the projects listed in `GITLAB_PROJECTS` or
  the `GITLAB_MAX_PROJECTS` most recently active ones. It downloads each one as a zip **at its current commit sha**,
  so citations stay valid even after new pushes. No git credentials are stored, and archive extraction
  refuses path-traversal entries.
- `local_source.py` indexes repositories that are already cloned (useful for testing). It derives the GitLab URL
  from `git remote` and strips any embedded credentials.

**File: `graphrag_gitlab/ingest/__init__.py`**

````python
(empty file)
````

**File: `graphrag_gitlab/ingest/gitlab_source.py`**

````python
"""Fetch a limited set of repositories from a GitLab group (e.g. MS-ClientServices).

Downloads a zip archive of each project at a pinned commit, so citations can be
permalinks (`/-/blob/<sha>/<path>#L10-20`) and no git credentials are needed.
"""
from __future__ import annotations

import io
import logging
import shutil
import zipfile
from pathlib import Path

from ..config import Settings
from ..models import RepoInfo

log = logging.getLogger(__name__)


def _select_projects(gl, settings: Settings):
    group = gl.groups.get(settings.gitlab_group)
    group_projects = list(
        group.projects.list(include_subgroups=True, archived=False, iterator=True, order_by="last_activity_at")
    )
    if settings.gitlab_projects:
        wanted = {p.lower() for p in settings.gitlab_projects}
        chosen = [
            gp for gp in group_projects
            if gp.path_with_namespace.lower() in wanted or gp.path.lower() in wanted or gp.name.lower() in wanted
        ]
        missing = wanted - {c.path_with_namespace.lower() for c in chosen} - {c.path.lower() for c in chosen} \
            - {c.name.lower() for c in chosen}
        if missing:
            log.warning("Projects not found in group %s: %s", settings.gitlab_group, ", ".join(sorted(missing)))
    else:
        chosen = group_projects[: settings.gitlab_max_projects]
    return chosen


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract, stripping the archive's top-level folder and refusing path traversal."""
    dest = dest.resolve()
    for member in zf.infolist():
        parts = Path(member.filename).parts
        if len(parts) <= 1 or member.is_dir():
            continue
        target = (dest / Path(*parts[1:])).resolve()
        if dest not in target.parents:
            log.warning("Skipping suspicious archive entry %s", member.filename)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def fetch_repos(settings: Settings) -> list[RepoInfo]:
    import gitlab  # python-gitlab

    if not settings.gitlab_url:
        raise SystemExit("GITLAB_URL is not set (your company GitLab, e.g. https://gitlab.company.internal).")
    if not settings.gitlab_token:
        raise SystemExit("GITLAB_TOKEN is not set (needs read_api + read_repository scopes).")

    gl = gitlab.Gitlab(settings.gitlab_url, private_token=settings.gitlab_token, ssl_verify=settings.gitlab_verify_ssl)
    gl.auth()

    repos: list[RepoInfo] = []
    for gp in _select_projects(gl, settings):
        project = gl.projects.get(gp.id)
        ref = settings.gitlab_ref or project.default_branch
        if not ref:
            log.warning("Skipping %s: empty repository", project.path_with_namespace)
            continue
        sha = project.commits.get(ref).id
        dest = settings.repos_dir / project.path_with_namespace.replace("/", "__")
        log.info("Downloading %s @ %s (%s)", project.path_with_namespace, ref, sha[:10])
        archive = project.repository_archive(sha=sha, format="zip")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            _safe_extract(zf, dest)
        repos.append(
            RepoInfo(
                name=project.path,
                full_path=project.path_with_namespace,
                local_path=str(dest),
                web_url=project.web_url,
                ref=sha,
                description=project.description or "",
            )
        )
    return repos
````

**File: `graphrag_gitlab/ingest/local_source.py`**

````python
"""Use already-cloned repositories on disk (handy for offline demos and tests)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..models import RepoInfo


def _git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _remote_to_web_url(remote: str | None) -> str | None:
    if not remote:
        return None
    remote = re.sub(r"\.git$", "", remote)
    m = re.match(r"git@([^:]+):(.+)", remote)  # git@gitlab.example.com:group/repo
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"https?://(?:[^@/]+@)?(.+)", remote)  # strip embedded credentials
    return f"https://{m.group(1)}" if m else None


def discover(paths: list[str]) -> list[RepoInfo]:
    repos = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"Not a directory: {path}")
        web_url = _remote_to_web_url(_git(path, "remote", "get-url", "origin"))
        full_path = web_url.split("/", 3)[3] if web_url and web_url.count("/") >= 3 else path.name
        repos.append(
            RepoInfo(
                name=path.name,
                full_path=full_path,
                local_path=str(path),
                web_url=web_url,
                ref=_git(path, "rev-parse", "HEAD"),
            )
        )
    return repos
````

---

## 11. Phase 7: Code parsing

**What and why.**
- **Python:** exact, using the standard-library `ast`. Extracts classes, functions, methods, docstrings, decorators,
  imports, base classes and calls. Calls use three forms: `foo` (plain or `self.` call), `.foo` (unknown receiver)
  and `Type.foo` (receiver type inferred from `self.x = Type()`, `x = Type()` or type annotations).
- **Other languages** (Java, C#, Kotlin, Scala, JS/TS, Go, PHP, Ruby, Rust, C/C++): regex for definitions, brace
  matching for block ends (`end` keywords for Ruby), language-specific import patterns, and leading doc comments.
- **Docs:** Markdown is split into sections by heading. **Config files** (YAML, Dockerfile, SQL and so on) become file chunks.
- Skips `node_modules`, `vendor`, `build`, `dist`, `.git`, virtual environments, minified files, lock files,
  binaries and files over `MAX_FILE_BYTES`.

**File: `graphrag_gitlab/parsing/__init__.py`**

````python
"""Walk a repository and turn every relevant file into a FileInfo with Symbols."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..models import FileInfo, RepoInfo, Symbol, file_id, symbol_id
from .generic_parser import parse_generic
from .python_parser import parse_python

log = logging.getLogger(__name__)

CODE_EXT = {
    ".py": "python", ".java": "java", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".cs": "csharp",
    ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".php": "php", ".rb": "ruby", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
}
DOC_EXT = {".md": "markdown", ".rst": "rst", ".txt": "text", ".adoc": "asciidoc"}
CONFIG_EXT = {
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".xml": "xml", ".properties": "properties",
    ".toml": "toml", ".gradle": "gradle", ".sql": "sql", ".sh": "shell", ".tf": "terraform", ".graphql": "graphql",
    ".proto": "protobuf",
}
CONFIG_NAMES = {"Dockerfile": "dockerfile", "Makefile": "make", "Jenkinsfile": "groovy", ".gitlab-ci.yml": "yaml"}
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "out", "bin", "obj", "vendor", "venv", ".venv", "__pycache__",
    ".idea", ".vscode", ".gradle", "coverage", ".next", ".nuxt", "site-packages", ".tox", ".mypy_cache",
}
SKIP_FILES = re.compile(r"(\.min\.js|\.lock|-lock\.json|\.map|\.snap)$")
FILE_SUMMARY_LINES = 60
DOC_SECTION_CHARS = 2500


def language_of(path: str) -> tuple[str | None, str]:
    """Return (language, category) where category is code | doc | config | ''."""
    name = os.path.basename(path)
    if name in CONFIG_NAMES:
        return CONFIG_NAMES[name], "config"
    ext = os.path.splitext(name)[1].lower()
    if ext in CODE_EXT:
        return CODE_EXT[ext], "code"
    if ext in DOC_EXT:
        return DOC_EXT[ext], "doc"
    if ext in CONFIG_EXT:
        return CONFIG_EXT[ext], "config"
    return None, ""


def iter_files(root: Path, max_bytes: int):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            lang, cat = language_of(rel)
            if not lang or SKIP_FILES.search(fn):
                continue
            try:
                if full.stat().st_size > max_bytes:
                    continue
                raw = full.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue  # binary
            yield rel, lang, cat, raw.decode("utf-8", errors="replace")


def _doc_sections(repo: str, path: str, lang: str, text: str) -> list[Symbol]:
    """Split markdown-ish docs on headings; long sections are windowed."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^#{1,4}\s", l)] if lang == "markdown" else []
    if not starts or starts[0] != 0:
        starts = [0] + starts
    out = []
    for n, s in enumerate(starts):
        e = (starts[n + 1] if n + 1 < len(starts) else len(lines)) - 1
        body = "\n".join(lines[s: e + 1]).strip()
        if not body:
            continue
        title = lines[s].lstrip("# ").strip() if lines[s].startswith("#") else os.path.basename(path)
        out.append(Symbol(
            id=symbol_id(repo, path, f"doc:{title}", s + 1), repo=repo, path=path, name=title,
            qualname=f"{os.path.basename(path)} § {title}", kind="doc", language=lang,
            start_line=s + 1, end_line=e + 1, code=body[:DOC_SECTION_CHARS], parent=file_id(repo, path),
        ))
    return out


def _file_summary(repo: str, path: str, lang: str, text: str, info: FileInfo) -> Symbol:
    lines = text.splitlines()
    top_level = [s.qualname for s in info.symbols if s.kind in ("class", "function")][:40]
    head = "\n".join(lines[:FILE_SUMMARY_LINES])
    doc = f"Defines: {', '.join(top_level)}" if top_level else ""
    return Symbol(
        id=file_id(repo, path), repo=repo, path=path, name=os.path.basename(path), qualname=path,
        kind="file", language=lang, start_line=1, end_line=max(len(lines), 1), docstring=doc, code=head,
    )


def parse_repo(repo: RepoInfo, max_bytes: int) -> list[tuple[FileInfo, Symbol]]:
    """Return (FileInfo, file-summary Symbol) for every indexed file."""
    results = []
    for rel, lang, cat, text in iter_files(Path(repo.local_path), max_bytes):
        if cat == "code":
            info = parse_python(repo.name, rel, text) if lang == "python" else parse_generic(repo.name, rel, text, lang)
        else:
            info = FileInfo(repo=repo.name, path=rel, language=lang)
            if cat == "doc":
                info.symbols = _doc_sections(repo.name, rel, lang, text)
        summary = _file_summary(repo.name, rel, lang, text, info)
        results.append((info, summary))
    log.info("Parsed %s: %d files, %d symbols", repo.name, len(results), sum(len(i.symbols) for i, _ in results))
    return results
````

**File: `graphrag_gitlab/parsing/python_parser.py`**

````python
"""Precise Python parsing via the standard-library AST."""
from __future__ import annotations

import ast

from ..models import FileInfo, Symbol, file_id, symbol_id

MAX_SYMBOL_LINES = 150


def _ctor_name(value: ast.AST) -> str | None:
    """`Foo(...)` / `mod.Foo(...)` -> "Foo" when it looks like a class instantiation."""
    if isinstance(value, ast.Call):
        fn = value.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
        if name and name[:1].isupper():
            return name
    return None


def _self_attr_types(cls: ast.ClassDef) -> dict[str, str]:
    """Infer `self.x` types from `self.x = Foo()` and `self.x: Foo` inside the class."""
    types: dict[str, str] = {}
    for sub in ast.walk(cls):
        targets, value, ann = [], None, None
        if isinstance(sub, ast.Assign):
            targets, value = sub.targets, sub.value
        elif isinstance(sub, ast.AnnAssign):
            targets, value, ann = [sub.target], sub.value, sub.annotation
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                typ = _ctor_name(value) or (ann.id if isinstance(ann, ast.Name) else None)
                if typ:
                    types[t.attr] = typ
    return types


def _call_names(node: ast.AST, attr_types: dict[str, str] | None = None) -> list[str]:
    """Callee names. Forms: "foo" (plain/self call), ".foo" (unknown receiver), "Type.foo" (typed receiver)."""
    attr_types = attr_types or {}
    local_types: dict[str, str] = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            typ = _ctor_name(sub.value)
            if typ:
                local_types[sub.targets[0].id] = typ
        elif isinstance(sub, ast.arg) and isinstance(sub.annotation, ast.Name) and sub.annotation.id[:1].isupper():
            local_types[sub.arg] = sub.annotation.id
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name):
                names.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                recv = fn.value
                if (isinstance(recv, ast.Attribute) and isinstance(recv.value, ast.Name)
                        and recv.value.id == "self" and recv.attr in attr_types):
                    names.append(f"{attr_types[recv.attr]}.{fn.attr}")
                    continue
                if isinstance(recv, ast.Name) and recv.id in local_types:
                    names.append(f"{local_types[recv.id]}.{fn.attr}")
                    continue
                # self.foo() is a same-class call; obj.foo() is marked with a leading dot
                is_self = isinstance(fn.value, ast.Name) and fn.value.id in ("self", "cls")
                names.append(fn.attr if is_self else "." + fn.attr)
    return sorted(set(names))


def _base_name(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Subscript):  # Generic[T]
        return _base_name(expr.value)
    return ""


def _signature(lines: list[str], node: ast.AST) -> str:
    start = node.lineno - 1
    sig = []
    for line in lines[start: start + 6]:
        sig.append(line.strip())
        if line.rstrip().endswith(":"):
            break
    return " ".join(sig)


def parse_python(repo: str, path: str, text: str) -> FileInfo:
    info = FileInfo(repo=repo, path=path, language="python")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return info
    lines = text.splitlines()
    fid = file_id(repo, path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # relative imports keep their dots so the resolver can anchor them to this file
            mod = "." * node.level + (node.module or "")
            info.imports.append(mod)
            info.imports.extend(f"{mod}.{a.name}" if mod and not mod.endswith(".") else f"{mod}{a.name}"
                                for a in node.names if a.name != "*")

    def visit(body: list[ast.stmt], parent_qual: str, parent_id: str, in_class: bool,
              attr_types: dict[str, str] | None = None) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{parent_qual}.{node.name}" if parent_qual else node.name
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                end = node.end_lineno or node.lineno
                is_class = isinstance(node, ast.ClassDef)
                kind = "class" if is_class else ("method" if in_class else "function")
                code_lines = lines[start - 1: min(end, start - 1 + MAX_SYMBOL_LINES)]
                sym = Symbol(
                    id=symbol_id(repo, path, qual, start),
                    repo=repo,
                    path=path,
                    name=node.name,
                    qualname=qual,
                    kind=kind,
                    language="python",
                    start_line=start,
                    end_line=end,
                    signature=_signature(lines, node),
                    docstring=(ast.get_docstring(node) or "")[:800],
                    code="\n".join(code_lines),
                    parent=parent_id,
                    calls=[] if is_class else _call_names(node, attr_types if in_class else None),
                    bases=[b for b in (_base_name(e) for e in node.bases) if b] if is_class else [],
                )
                info.symbols.append(sym)
                visit(node.body, qual, sym.id, in_class=is_class,
                      attr_types=_self_attr_types(node) if is_class else None)

    visit(tree.body, "", fid, in_class=False)
    return info
````

**File: `graphrag_gitlab/parsing/generic_parser.py`**

````python
"""Lightweight regex + brace-matching parser for C-family and other languages.

Good enough for a prototype across Java, C#, Kotlin, Scala, JS/TS, Go, PHP, Rust,
C/C++, Ruby. Swap in tree-sitter later for exact ASTs without changing the graph.
"""
from __future__ import annotations

import re

from ..models import FileInfo, Symbol, file_id, symbol_id

MAX_SYMBOL_LINES = 150

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "else", "do", "try", "finally", "throw",
    "sizeof", "typeof", "function", "super", "this", "await", "yield", "case", "using", "lock", "foreach",
    "synchronized", "elif", "when", "match", "with", "assert", "print", "require", "import", "defer", "go",
    "select", "func", "fn", "def", "class", "struct", "interface", "enum", "where", "constructor",
}

CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:(?:public|private|protected|internal|abstract|final|static|sealed|"
    r"partial|data|open|inner|pub(?:\(crate\))?)\s+)*(class|interface|enum|struct|record|trait|object|impl)\s+"
    r"([A-Za-z_]\w*)(.*)$"
)
GO_TYPE_RE = re.compile(r"^type\s+([A-Za-z_]\w*)\s+(struct|interface)\b")

FUNC_RES = {
    "javascript": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?"
                   r"(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)"),
        re.compile(r"^\s*(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*"
                   r"([A-Za-z_$][\w$]*)\s*\([^)]*\)?\s*(?::\s*[^{=]+)?\{\s*$"),
    ],
    "c_family": [
        re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|internal|static|final|abstract|"
                   r"synchronized|override|virtual|async|extern|inline|unsafe|native|default)\s+)*"
                   r"(?:[\w<>\[\],.?*&:]+\s+)+([A-Za-z_]\w*)\s*\([^;]*$"),
    ],
    "kotlin": [re.compile(r"^\s*(?:(?:public|private|protected|internal|override|suspend|inline|open|operator)\s+)*"
                          r"fun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(")],
    "scala": [re.compile(r"^\s*(?:(?:override|private|protected|final|implicit)\s+)*def\s+([A-Za-z_]\w*)")],
    "go": [re.compile(r"^func\s+(?:\(\s*\w*\s*\*?(\w+)[^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]")],
    "rust": [re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+([A-Za-z_]\w*)")],
    "php": [re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?([A-Za-z_]\w*)\s*\(")],
    "ruby": [re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)")],
}
FAMILY = {
    "javascript": "javascript", "typescript": "javascript",
    "java": "c_family", "csharp": "c_family", "c": "c_family", "cpp": "c_family",
    "kotlin": "kotlin", "scala": "scala", "go": "go", "rust": "rust", "php": "php", "ruby": "ruby",
}

IMPORT_RES = {
    "java": [r"^\s*import\s+(?:static\s+)?([\w.]+)"],
    "kotlin": [r"^\s*import\s+([\w.]+)"],
    "scala": [r"^\s*import\s+([\w.]+)"],
    "javascript": [r"""(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]""", r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
                   r"""^\s*import\s+['"]([^'"]+)['"]"""],
    "typescript": [r"""(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]""", r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
                   r"""^\s*import\s+['"]([^'"]+)['"]"""],
    "go": [r"""^\s*import\s+(?:\w+\s+)?"([^"]+)\"""", r"""^\s+(?:\w+\s+)?"([^"]+)"\s*$"""],
    "csharp": [r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;"],
    "php": [r"^\s*use\s+([\w\\]+)"],
    "ruby": [r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]"""],
    "rust": [r"^\s*use\s+([\w:]+)"],
    "c": [r"""^\s*#include\s+[<"]([^>"]+)[>"]"""],
    "cpp": [r"""^\s*#include\s+[<"]([^>"]+)[>"]"""],
}
# groups: receiver, separator, callee  —  foo(  |  obj.foo(  |  this.foo(  |  $this->foo(  |  Cls::foo(
CALL_RE = re.compile(r"(?<![\w$])(?:([\w$]+)\s*(\.|->|::)\s*)?([A-Za-z_$][\w$]*)\s*\(")
SELF_RECEIVERS = {"this", "self", "$this", "super"}
COMMENT_RE = re.compile(r"^\s*(//|/\*|\*|#|--)")
DOC_COMMENT_RE = re.compile(r"^\s*(///?|/\*\*?|\*|#)\s?")


def _block_end(lines: list[str], start: int, language: str) -> int:
    """Return the 0-based index of the last line of the block that starts at `start`."""
    if language == "ruby":
        indent = len(lines[start]) - len(lines[start].lstrip())
        for i in range(start + 1, len(lines)):
            s = lines[i]
            if s.strip() == "end" and len(s) - len(s.lstrip()) == indent:
                return i
        return start
    depth, opened = 0, False
    for i in range(start, len(lines)):
        line = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*$', "", lines[i])
        if not opened and i > start + 3:
            return start  # declaration without a body (interface method, prototype)
        if not opened and ";" in line and "{" not in line and i == start and "(" in line and ")" in line:
            return start  # one-line declaration
        for ch in line:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return i
    return len(lines) - 1 if opened else start


def _leading_comment(lines: list[str], idx: int) -> str:
    out = []
    i = idx - 1
    while i >= 0 and (COMMENT_RE.match(lines[i]) or lines[i].strip().startswith("@")):
        if not lines[i].strip().startswith("@"):
            out.append(DOC_COMMENT_RE.sub("", lines[i]).strip(" */"))
        i -= 1
    return " ".join(reversed([o for o in out if o]))[:800]


def parse_generic(repo: str, path: str, text: str, language: str) -> FileInfo:
    info = FileInfo(repo=repo, path=path, language=language)
    lines = text.splitlines()
    fid = file_id(repo, path)

    for pattern in IMPORT_RES.get(language, []):
        info.imports.extend(m.group(1) for m in re.finditer(pattern, text, flags=re.MULTILINE))

    family = FAMILY.get(language)
    func_res = FUNC_RES.get(family, [])
    classes: list[Symbol] = []

    for i, line in enumerate(lines):
        if COMMENT_RE.match(line):
            continue
        kind, name, bases, receiver = None, None, [], None

        m = CLASS_RE.match(line)
        g = GO_TYPE_RE.match(line) if language == "go" else None
        if m and not line.rstrip().endswith(";"):
            kind, name = "class", m.group(2)
            rest = m.group(3)
            ext = re.search(r"(?:extends|implements|:|<)\s*([\w\s,.<>]+)", rest)
            if ext:
                bases = [b.split("<")[0].split(".")[-1] for b in re.split(r"[\s,]+", ext.group(1))
                         if b and b not in {"extends", "implements", "where"}]
        elif g:
            kind, name = "class", g.group(1)
        else:
            for rx in func_res:
                fm = rx.match(line)
                if fm:
                    if family == "go":
                        receiver, name = fm.group(1), fm.group(2)
                    else:
                        name = fm.group(1)
                    if name in KEYWORDS:
                        name = None
                        continue
                    kind = "function"
                    break
        if not kind or not name:
            continue

        end = _block_end(lines, i, language)
        if kind == "function" and end == i and family == "c_family" and not line.rstrip().endswith("{"):
            # likely a call/statement spanning lines rather than a definition
            if ")" in line and ";" in line:
                continue

        parent_cls = None
        if receiver:
            parent_cls = next((c for c in classes if c.name == receiver), None)
        else:
            enclosing = [c for c in classes if c.start_line - 1 < i <= c.end_line - 1]
            parent_cls = enclosing[-1] if enclosing else None
        if kind == "function" and parent_cls:
            kind = "method"
        qual = f"{parent_cls.qualname}.{name}" if parent_cls else (f"{receiver}.{name}" if receiver else name)

        body = lines[i: min(end + 1, i + MAX_SYMBOL_LINES)]
        calls = sorted({(("." if sep and recv not in SELF_RECEIVERS else "") + c)
                        for recv, sep, c in CALL_RE.findall("\n".join(body[1:] if kind != "class" else []))
                        if c not in KEYWORDS and c != name})
        sym = Symbol(
            id=symbol_id(repo, path, qual, i + 1),
            repo=repo,
            path=path,
            name=name,
            qualname=qual,
            kind=kind,
            language=language,
            start_line=i + 1,
            end_line=end + 1,
            signature=line.strip()[:300],
            docstring=_leading_comment(lines, i),
            code="\n".join(body),
            parent=parent_cls.id if parent_cls else fid,
            calls=calls if kind != "class" else [],
            bases=bases,
        )
        info.symbols.append(sym)
        if kind == "class":
            classes.append(sym)
    return info
````

---

## 12. Phase 8: Knowledge graph

**What and why.** See the schema in section 3.1. Import resolution handles Python relative and absolute imports,
JS/TS relative paths, Java/Kotlin/C# dotted names and Go package directories. An import that doesn't resolve becomes an
`ext:` dependency node, unless it looks like a package inside the repo. Call resolution order:
typed receiver, then same class, then same file, then imported files, then a unique name in the repo.

**File: `graphrag_gitlab/graph/__init__.py`**

````python
(empty file)
````

**File: `graphrag_gitlab/graph/builder.py`**

````python
"""Build the code knowledge graph.

Nodes:  repo:*, file:*, sym:* (class / function / method / doc), ext:* (external dependency)
Edges:  CONTAINS (repo→file), DEFINES (file→symbol, class→method), IMPORTS (file→file|ext),
        CALLS (function→function), INHERITS (class→class)
"""
from __future__ import annotations

import posixpath
from collections import defaultdict

import networkx as nx

from ..models import FileInfo, RepoInfo, Symbol, repo_id

CODE_SUFFIXES = (".py", ".java", ".kt", ".scala", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".cs", ".php",
                 ".rb", ".rs", ".c", ".h", ".cpp", ".hpp")


def _node_attrs(sym: Symbol) -> dict:
    return dict(kind=sym.kind, repo=sym.repo, path=sym.path, name=sym.name, qualname=sym.qualname,
                language=sym.language, start_line=sym.start_line, end_line=sym.end_line)


class _ImportResolver:
    """Map an import string to a file in the same repo (best effort, language agnostic)."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        self.by_stem: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            stem = p
            for suf in CODE_SUFFIXES:
                if stem.endswith(suf):
                    stem = stem[: -len(suf)]
                    break
            if stem.endswith("/__init__") or stem.endswith("/index"):
                self.by_stem[stem.rsplit("/", 1)[0]].append(p)
            self.by_stem[stem].append(p)
        self.dirs: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            self.dirs[posixpath.dirname(p)].append(p)

    def _match_suffix(self, key: str) -> list[str]:
        if key in self.by_stem:
            return self.by_stem[key]
        hits = [p for stem, ps in self.by_stem.items() if stem.endswith("/" + key) for p in ps]
        return hits if len(hits) <= 3 else []

    def resolve(self, importer: str, imp: str) -> list[str]:
        if not imp:
            return []
        here = posixpath.dirname(importer)
        if imp.startswith("./") or imp.startswith("../"):            # JS/TS relative
            return self._match_suffix(posixpath.normpath(posixpath.join(here, imp)))
        if imp.startswith("."):                                       # python relative
            level = len(imp) - len(imp.lstrip("."))
            base = here
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rest = imp.lstrip(".").replace(".", "/")
            return self._match_suffix(posixpath.join(base, rest) if rest else base)
        key = imp.replace("\\", "/").replace("::", "/").replace(".", "/").strip("/")
        if key.startswith("crate/"):
            key = key[6:]
        hits = self._match_suffix(key)
        if not hits and "/" in key:                                   # Go package dir / Java wildcard
            d = key.rstrip("/*")
            hits = [p for dpath, ps in self.dirs.items() if dpath == d or dpath.endswith("/" + d) for p in ps][:20]
        return hits

    def is_internal(self, name: str) -> bool:
        """True if `name` (a/b/c) looks like a package that lives inside this repo."""
        needle = "/" + name.strip("/") + "/"
        return any(needle in "/" + stem + "/" for stem in self.by_stem) or any(
            needle in "/" + d + "/" for d in self.dirs)


# Attribute calls with these names are almost always stdlib/container methods, not project code
COMMON_METHODS = {
    "get", "set", "put", "add", "append", "extend", "insert", "remove", "pop", "push", "update", "clear", "copy",
    "keys", "values", "items", "join", "split", "strip", "format", "replace", "lower", "upper", "startswith",
    "endswith", "read", "write", "close", "open", "map", "filter", "reduce", "forEach", "toString", "equals",
    "hashCode", "length", "size", "isEmpty", "contains", "trim", "then", "catch", "log", "info", "debug", "warn",
    "error", "print", "println", "printf", "encode", "decode", "parse", "stringify", "sort", "count", "index",
}


def _ext_name(imp: str) -> str:
    imp = imp.lstrip("@").replace("\\", "/").replace("::", "/").replace(".", "/")
    parts = [p for p in imp.split("/") if p]
    if not parts:
        return ""
    if parts[0] in {"com", "org", "io", "net", "github", "gitlab", "golang", "microsoft", "system"} and len(parts) > 2:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if parts[0] in {"github", "gitlab"} else parts[0]


def build_graph(repos: list[RepoInfo], parsed: dict[str, list[tuple[FileInfo, Symbol]]]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for repo in repos:
        rid = repo_id(repo.name)
        g.add_node(rid, kind="repo", repo=repo.name, name=repo.name, qualname=repo.full_path, path="")
        files = parsed.get(repo.name, [])
        resolver = _ImportResolver([f.path for f, _ in files])
        by_name: dict[str, list[Symbol]] = defaultdict(list)
        file_imports: dict[str, set[str]] = defaultdict(set)

        for info, summary in files:
            g.add_node(summary.id, **_node_attrs(summary))
            g.add_edge(rid, summary.id, type="CONTAINS")
            for sym in info.symbols:
                g.add_node(sym.id, **_node_attrs(sym))
                g.add_edge(sym.parent or summary.id, sym.id, type="DEFINES")
                if sym.kind != "doc":
                    by_name[sym.name].append(sym)

        for info, summary in files:
            for imp in dict.fromkeys(info.imports):
                targets = [t for t in resolver.resolve(info.path, imp) if t != info.path]
                if targets:
                    for t in targets:
                        tid = f"file:{repo.name}:{t}"
                        if t not in file_imports[info.path]:
                            g.add_edge(summary.id, tid, type="IMPORTS")
                            file_imports[info.path].add(t)
                else:
                    ext = _ext_name(imp)
                    if ext and not ext.startswith(".") and not resolver.is_internal(ext):
                        eid = f"ext:{ext}"
                        if eid not in g:
                            g.add_node(eid, kind="external", name=ext, qualname=ext, repo="", path="")
                        if not g.has_edge(summary.id, eid):
                            g.add_edge(summary.id, eid, type="IMPORTS")

        for info, summary in files:
            imported = file_imports[info.path]
            for sym in info.symbols:
                for base in sym.bases:
                    for target in by_name.get(base, []):
                        if target.kind == "class" and target.id != sym.id:
                            g.add_edge(sym.id, target.id, type="INHERITS")
                for raw in sym.calls:
                    if "." in raw.lstrip("."):  # typed receiver: "Type.method"
                        typ, meth = raw.rsplit(".", 1)
                        for c in by_name.get(meth, []):
                            if c.qualname == f"{typ}.{meth}" or c.qualname.endswith(f".{typ}.{meth}"):
                                g.add_edge(sym.id, c.id, type="CALLS")
                        continue
                    is_attr = raw.startswith(".")
                    callee = raw.lstrip(".")
                    cands = [c for c in by_name.get(callee, []) if c.id != sym.id and c.kind != "file"]
                    if not cands:
                        continue
                    same_file = [c for c in cands if c.path == sym.path]
                    if is_attr and callee in COMMON_METHODS and not same_file:
                        continue
                    if not is_attr and sym.kind == "method":  # self.foo(): prefer the same class
                        same_class = [c for c in same_file if c.parent == sym.parent]
                        same_file = same_class or same_file
                    via_import = [c for c in cands if c.path in imported]
                    chosen = same_file or via_import or (cands if len(cands) <= 2 else [])
                    for c in chosen:
                        g.add_edge(sym.id, c.id, type="CALLS")
    return g
````

---

## 13. Phase 9: Hybrid search index

**What and why.** `CodeIndex` holds the chunks, the graph, BM25 and the vectors, and saves everything to `data/index/` as
JSON, JSONL and NPY. There is no database server. On load it uses the **same embedder that built the index**.

**File: `graphrag_gitlab/index/__init__.py`**

````python
(empty file)
````

**File: `graphrag_gitlab/index/text.py`**

````python
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
````

**File: `graphrag_gitlab/index/embeddings.py`**

````python
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
````

**File: `graphrag_gitlab/index/store.py`**

````python
"""CodeIndex: knowledge graph + chunk store + BM25 + vectors, with hybrid search and graph expansion."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np
from rank_bm25 import BM25Okapi

from ..models import RepoInfo, Symbol
from .embeddings import make_embedder
from .text import tokenize

log = logging.getLogger(__name__)

RRF_K = 60
# How strongly relevance flows along each edge type during graph expansion (out, in)
EDGE_WEIGHTS = {
    "CALLS": (0.65, 0.55),      # callee / caller
    "DEFINES": (0.45, 0.55),    # child member / enclosing class-file
    "INHERITS": (0.5, 0.45),
    "IMPORTS": (0.3, 0.3),
    "CONTAINS": (0.0, 0.0),
}
KIND_PRIOR = {"function": 1.0, "method": 1.0, "class": 0.95, "file": 0.8, "doc": 0.85}


@dataclass
class Hit:
    id: str
    score: float
    via: str = "search"                       # search | symbol | graph
    reasons: list[str] = field(default_factory=list)


class CodeIndex:
    def __init__(self, repos: dict[str, RepoInfo], chunks: dict[str, Symbol], graph: nx.MultiDiGraph,
                 vectors: np.ndarray, embedder, meta: dict):
        self.repos = repos
        self.chunks = chunks
        self.graph = graph
        self.vectors = vectors
        self.embedder = embedder
        self.meta = meta
        self.ids = list(chunks.keys())
        self.pos = {cid: i for i, cid in enumerate(self.ids)}
        self.repo_of = np.array([chunks[c].repo for c in self.ids])
        self.bm25 = BM25Okapi([tokenize(chunks[c].index_text()) or ["_"] for c in self.ids])
        self.name_index: dict[str, list[str]] = defaultdict(list)
        for cid, ch in chunks.items():
            if ch.kind != "doc":
                self.name_index[ch.name.lower()].append(cid)
                self.name_index[ch.qualname.lower()].append(cid)

    # ------------------------------------------------------------------ persistence
    @classmethod
    def build(cls, repos: list[RepoInfo], chunks: list[Symbol], graph: nx.MultiDiGraph, settings) -> "CodeIndex":
        embedder = make_embedder(settings.embedding_backend, settings.embedding_model)
        log.info("Embedding %d chunks with %s", len(chunks), embedder.name)
        vectors = embedder.embed_documents([c.index_text(1500) for c in chunks])
        meta = {"embedding_backend": embedder.name, "embedding_model": getattr(embedder, "model_name", None)}
        return cls({r.name: r for r in repos}, {c.id: c for c in chunks}, graph, vectors, embedder, meta)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "repos.json").write_text(json.dumps([r.to_dict() for r in self.repos.values()], indent=2))
        with open(directory / "chunks.jsonl", "w") as f:
            for cid in self.ids:
                f.write(json.dumps(self.chunks[cid].to_dict()) + "\n")
        (directory / "graph.json").write_text(json.dumps(nx.node_link_data(self.graph, edges="links")))
        np.save(directory / "vectors.npy", self.vectors)
        (directory / "meta.json").write_text(json.dumps(self.meta, indent=2))

    @classmethod
    def load(cls, directory: Path) -> "CodeIndex":
        if not (directory / "meta.json").exists():
            raise SystemExit(f"No index found in {directory}. Run `graphrag-gitlab ingest` first.")
        repos = {r["name"]: RepoInfo(**r) for r in json.loads((directory / "repos.json").read_text())}
        chunks = {}
        with open(directory / "chunks.jsonl") as f:
            for line in f:
                d = json.loads(line)
                chunks[d["id"]] = Symbol(**d)
        graph = nx.node_link_graph(json.loads((directory / "graph.json").read_text()), multigraph=True,
                                   directed=True, edges="links")
        meta = json.loads((directory / "meta.json").read_text())
        embedder = make_embedder(meta["embedding_backend"], meta.get("embedding_model") or "")
        if embedder.name != meta["embedding_backend"]:
            raise SystemExit(f"Index was built with {meta['embedding_backend']} embeddings; install it or re-ingest.")
        return cls(repos, chunks, graph, np.load(directory / "vectors.npy"), embedder, meta)

    # ------------------------------------------------------------------ search
    def _mask(self, repos: list[str] | None) -> np.ndarray:
        if not repos:
            return np.ones(len(self.ids), dtype=bool)
        return np.isin(self.repo_of, repos)

    def bm25_search(self, query: str, k: int, repos: list[str] | None = None) -> list[tuple[str, float]]:
        toks = tokenize(query)
        if not toks or not self.ids:
            return []
        scores = np.where(self._mask(repos), self.bm25.get_scores(toks), -np.inf)
        order = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in order if scores[i] > 0]

    def vector_search(self, query: str, k: int, repos: list[str] | None = None) -> list[tuple[str, float]]:
        if not len(self.ids):
            return []
        sims = np.where(self._mask(repos), self.vectors @ self.embedder.embed_query(query), -np.inf)
        order = np.argsort(-sims)[:k]
        floor = 0.15 if self.meta["embedding_backend"] == "hashing" else 0.3
        return [(self.ids[i], float(sims[i])) for i in order if sims[i] >= floor]

    def lookup_symbols(self, names: list[str], repos: list[str] | None = None) -> list[str]:
        out = []
        for n in names:
            key = n.strip("`()").lower()
            ids = self.name_index.get(key, []) or self.name_index.get(key.split(".")[-1], [])
            out.extend(i for i in ids if not repos or self.chunks[i].repo in repos)
        return list(dict.fromkeys(out))

    def hybrid_search(self, queries: list[str], k: int = 8, repos: list[str] | None = None,
                      symbols: list[str] | None = None) -> list[Hit]:
        """Reciprocal-rank fusion of BM25 + vector results over several query variants, plus exact symbol hits."""
        fused: dict[str, Hit] = {}

        def add(cid: str, score: float, via: str, reason: str) -> None:
            h = fused.setdefault(cid, Hit(cid, 0.0, via))
            h.score += score
            if reason not in h.reasons:
                h.reasons.append(reason)

        for q in queries:
            for rank, (cid, _) in enumerate(self.bm25_search(q, 40, repos)):
                add(cid, 1 / (RRF_K + rank), "search", "bm25")
            for rank, (cid, _) in enumerate(self.vector_search(q, 40, repos)):
                add(cid, 1 / (RRF_K + rank), "search", "vector")
        for cid in self.lookup_symbols(symbols or [], repos):
            add(cid, 2 / RRF_K, "symbol", "exact symbol match")
            fused[cid].via = "symbol"
        for h in fused.values():
            h.score *= KIND_PRIOR.get(self.chunks[h.id].kind, 0.8)
        return sorted(fused.values(), key=lambda h: -h.score)[:k]

    # ------------------------------------------------------------------ graph
    def relations(self, cid: str, limit: int = 12) -> list[tuple[str, str, str]]:
        """(direction, edge_type, other_node) for a node, skipping structural noise."""
        out = []
        if cid not in self.graph:
            return out
        for _, dst, data in self.graph.out_edges(cid, data=True):
            if data["type"] != "CONTAINS":
                out.append(("out", data["type"], dst))
        for src, _, data in self.graph.in_edges(cid, data=True):
            if data["type"] not in ("CONTAINS",) and not src.startswith("repo:"):
                out.append(("in", data["type"], src))
        return out[:limit]

    def expand(self, seeds: list[Hit], hops: int = 1, limit: int = 8,
               edge_types: set[str] | None = None) -> list[Hit]:
        """Spread relevance from seed chunks over the graph; returns new neighbor hits.

        Seeds that are directly connected to a higher-ranked seed get a boost (graph-confirmed relevance),
        so callers should re-sort `seeds` afterwards.
        """
        seen = {h.id for h in seeds}
        seed_by_id = {h.id: h for h in seeds}
        rank = {h.id: i for i, h in enumerate(seeds)}
        frontier = {h.id: h.score for h in seeds}
        found: dict[str, Hit] = {}
        for hop in range(hops):
            nxt: dict[str, float] = {}
            for nid, score in frontier.items():
                for direction, etype, other in self.relations(nid, limit=40):
                    if edge_types and etype not in edge_types:
                        continue
                    if other not in self.chunks:
                        continue
                    w = EDGE_WEIGHTS[etype][0 if direction == "out" else 1]
                    s = score * w
                    if s <= 0:
                        continue
                    label = self.chunks[nid].qualname
                    verb = {("out", "CALLS"): "called by", ("in", "CALLS"): "calls",
                            ("out", "DEFINES"): "member of", ("in", "DEFINES"): "defines",
                            ("out", "INHERITS"): "base class of", ("in", "INHERITS"): "subclass of",
                            ("out", "IMPORTS"): "imported by", ("in", "IMPORTS"): "imports"}[(direction, etype)]
                    if other in seed_by_id:
                        if hop == 0 and rank.get(nid, 1e9) < rank[other]:
                            seed_by_id[other].score += s * 0.5
                            seed_by_id[other].reasons.append(f"{verb} {label}")
                        continue
                    if other in seen:
                        continue
                    h = found.setdefault(other, Hit(other, 0.0, "graph"))
                    h.score = max(h.score, s)
                    reason = f"{verb} {label}"
                    if reason not in h.reasons:
                        h.reasons.append(reason)
                    nxt[other] = max(nxt.get(other, 0.0), s)
            seen |= set(nxt)
            frontier = nxt
        return sorted(found.values(), key=lambda h: -h.score)[:limit]

    def repo_summary(self, hits: list[Hit]) -> list[dict]:
        agg: dict[str, dict] = {}
        for h in hits:
            ch = self.chunks[h.id]
            a = agg.setdefault(ch.repo, {"repo": ch.repo, "score": 0.0, "paths": []})
            a["score"] += h.score
            if ch.path not in a["paths"]:
                a["paths"].append(ch.path)
        for a in agg.values():
            r = self.repos.get(a["repo"])
            a["full_path"] = r.full_path if r else a["repo"]
            a["web_url"] = r.web_url if r else None
        return sorted(agg.values(), key=lambda a: -a["score"])

    def citation_url(self, cid: str) -> str | None:
        ch = self.chunks[cid]
        repo = self.repos.get(ch.repo)
        return repo.blob_url(ch.path, ch.start_line, ch.end_line) if repo else None

    def stats(self) -> dict:
        kinds: dict[str, int] = defaultdict(int)
        for ch in self.chunks.values():
            kinds[ch.kind] += 1
        etypes: dict[str, int] = defaultdict(int)
        for _, _, d in self.graph.edges(data=True):
            etypes[d["type"]] += 1
        return {"repos": list(self.repos), "chunks": dict(kinds), "nodes": self.graph.number_of_nodes(),
                "edges": dict(etypes), **self.meta}
````

---

## 14. Phase 10: Local LLM client and prompts

**What and why.**
- `OllamaLLM` talks only to the configured local Ollama server.
- It **always sends `num_ctx`**. Ollama's default context is small and silently truncates long RAG prompts.
- It streams tokens, so the CLI and UI show text as it's generated.
- It supports JSON-schema output through Ollama's `format` parameter, used by the optional LLM planner.
- It maps every failure (Ollama not running, model not pulled, HTTP errors) to `LLMUnavailable`. The agent then
  falls back to a retrieval-only answer instead of crashing.
- The prompts are short and explicit, written for small local models.

**File: `graphrag_gitlab/agent/__init__.py`**

````python
(empty file)
````

**File: `graphrag_gitlab/agent/llm.py`**

````python
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
````

**File: `graphrag_gitlab/agent/prompts.py`**

````python
"""Prompts written for small local models: short, explicit, one job each."""

PLANNER_SYSTEM = """You plan code searches. Read the developer's question and return JSON.

intent: one of
- locate: where is something implemented
- related: which files or functions relate to a feature
- explain: how does a function, class or service work
- repos: which repositories contain code for a requirement
- general: anything else

search_queries: 2-4 short search phrases. Include words a programmer would use in code for this idea,
for example "refund" -> "refund payment", "RefundService", "reverse transaction".
symbols: function, class or file names written in the question. Empty list if none.
repos: repository names from the given list only if the question names them. Usually empty."""

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["locate", "related", "explain", "repos", "general"]},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "symbols": {"type": "array", "items": {"type": "string"}},
        "repos": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "search_queries", "symbols", "repos"],
}

REWRITE_SYSTEM = """A code search found nothing. Suggest 3-5 different short search phrases for the same
question: synonyms and likely function or class names. Return JSON."""

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"search_queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["search_queries"],
}

ANSWER_SYSTEM = """You are a code assistant. Answer the developer's question using ONLY the numbered sources.
Each source is code or documentation from a GitLab repository, with its relations
(→ CALLS means it calls that function, ← CALLS means it is called by it).

Rules:
1. Start with the direct answer: repository, file and function or class name.
2. After every fact, cite the source like [S1] or [S2]. Only use source numbers that exist.
3. For "how does it work" questions, explain the steps in order, following the calls between sources.
4. For "which repositories" questions, list each repository with one line and a citation.
5. You may quote a few lines of code from a source in a fenced code block. Never invent code.
6. If the sources do not answer the question, say so clearly. Do not guess.
7. Be brief. Use short paragraphs or bullet points. Do not list the sources at the end."""
````

---

## 15. Phase 11: LangGraph agent

**What and why.** See section 3.3. CPU-specific choices:
- The rule-based planner is the default, which saves one slow LLM call per question.
- The context is about 6 chunks with a character budget derived from `NUM_CTX`. For `explain` and `related`
  questions, half the slots are reserved for graph neighbours, so the call chain makes it into the prompt.
- `validate_citations()` removes `[S#]` tags that point to sources that don't exist. Small models sometimes invent them.

**File: `graphrag_gitlab/agent/graph.py`**

````python
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
````

---

## 16. Phase 12: Pipeline, CLI and web UI

**File: `graphrag_gitlab/pipeline.py`**

````python
"""End-to-end ingestion: fetch repos → parse → build graph → index (BM25 + vectors) → save."""
from __future__ import annotations

import logging

from .config import Settings
from .graph.builder import build_graph
from .index.store import CodeIndex
from .models import RepoInfo
from .parsing import parse_repo

log = logging.getLogger(__name__)


def ingest(settings: Settings, source: str = "gitlab", local_paths: list[str] | None = None) -> CodeIndex:
    if source == "gitlab":
        from .ingest.gitlab_source import fetch_repos

        repos = fetch_repos(settings)
    else:
        from .ingest.local_source import discover

        repos = discover(local_paths or [])
    if not repos:
        raise SystemExit("No repositories selected.")
    return build_index(settings, repos)


def build_index(settings: Settings, repos: list[RepoInfo]) -> CodeIndex:
    names = [r.name for r in repos]
    for r in repos:  # same project name in two subgroups -> use the full path to keep ids unique
        if names.count(r.name) > 1:
            r.name = r.full_path.replace("/", "__")
    parsed = {}
    chunks = []
    for repo in repos:
        files = parse_repo(repo, settings.max_file_bytes)
        parsed[repo.name] = files
        for info, summary in files:
            chunks.append(summary)
            chunks.extend(info.symbols)
    graph = build_graph(repos, parsed)
    index = CodeIndex.build(repos, chunks, graph, settings)
    index.save(settings.index_dir)
    log.info("Index saved to %s: %s", settings.index_dir, index.stats())
    return index
````

**CLI commands**

| Command | Purpose |
|---|---|
| `graphrag-gitlab setup-models` | One-time, online: caches the embedding model |
| `graphrag-gitlab doctor` | Checks Ollama, the model, offline mode, telemetry lockdown, endpoints and the index |
| `graphrag-gitlab ingest [--projects a,b] [--max-projects N]` | Downloads from GitLab and builds the index |
| `graphrag-gitlab ingest --source local --path DIR [--path DIR2]` | Indexes local clones |
| `graphrag-gitlab ask "question" [--repo name] [--json]` | One question, streamed answer + sources |
| `graphrag-gitlab chat` | Interactive loop |
| `graphrag-gitlab related <symbol>` | Shows a symbol's graph neighbours |
| `graphrag-gitlab stats` | Index statistics |
| global flags: `-v` (agent trace), `--no-llm` (retrieval only) | |

**File: `graphrag_gitlab/cli.py`**

````python
"""Command-line interface: setup-models | doctor | ingest | ask | chat | stats | related."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .config import Settings
from .privacy import LOCKDOWN_ENV, check_endpoints


def _assistant(settings: Settings):
    from .agent.graph import CodeAssistant
    from .agent.llm import make_llm
    from .index.store import CodeIndex

    index = CodeIndex.load(settings.index_dir)
    return CodeAssistant(index, make_llm(settings), top_k=settings.top_k, max_context=settings.max_context_chunks,
                         max_snippet_lines=settings.max_snippet_lines, use_llm_planner=settings.use_llm_planner,
                         num_ctx=settings.num_ctx)


def _ask_and_print(assistant, question: str, repos: list[str], verbose: bool) -> None:
    from .agent.graph import format_sources

    streamed = []

    def on_token(t: str) -> None:
        streamed.append(t)
        print(t, end="", flush=True)

    result = assistant.ask(question, repos, on_token=on_token)
    if verbose:
        print("\n".join(f"  · {t}" for t in result.get("trace", [])), file=sys.stderr)
    if result.get("llm_note"):
        print(f"[note] {result['llm_note']}", file=sys.stderr)
    if streamed:
        print()
        if "".join(streamed) != result["answer"]:  # invalid citations were removed after streaming
            print("[note] removed citations to sources that do not exist", file=sys.stderr)
    else:
        print(result["answer"])
    src = format_sources(result)
    if src:
        print("\nSources:\n" + src)


def cmd_setup_models(settings: Settings) -> None:
    """One-time, online: cache the embedding model locally. Ollama models are pulled with `ollama pull`."""
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(key, None)  # must happen before huggingface_hub is imported
    if settings.embedding_backend == "sentence-transformers":
        from sentence_transformers import SentenceTransformer

        print(f"Downloading embedding model {settings.embedding_model} (one time)…")
        SentenceTransformer(settings.embedding_model)
        print("Cached. From now on it loads offline.")
    print(f"Now run:  ollama pull {settings.ollama_model}")


def cmd_doctor(settings: Settings) -> None:
    ok = True

    def report(good: bool, msg: str) -> None:
        nonlocal ok
        ok &= good
        print(f"[{'OK' if good else 'FAIL'}] {msg}")

    try:
        check_endpoints(settings)
        report(True, f"LLM endpoint is local/internal: {settings.ollama_host}")
    except SystemExit as e:
        report(False, str(e))
    leaks = [k for k, v in LOCKDOWN_ENV.items() if os.environ.get(k) != v]
    report(not leaks, "telemetry/tracing disabled" + (f" (not applied: {leaks})" if leaks else ""))
    report(os.environ.get("HF_HUB_OFFLINE") == "1", "HuggingFace offline mode on")

    import ollama

    try:
        models = [m.model for m in ollama.Client(host=settings.ollama_host).list().models]
        report(True, f"Ollama reachable ({len(models)} models)")
        report(any(m == settings.ollama_model or m.split(":")[0] == settings.ollama_model for m in models),
               f"model {settings.ollama_model} pulled" + ("" if models else " — run `ollama pull`"))
    except Exception as e:  # noqa: BLE001 - diagnostics
        report(False, f"Ollama not reachable at {settings.ollama_host}: {e}")

    if settings.embedding_backend == "sentence-transformers":
        try:
            from sentence_transformers import SentenceTransformer

            SentenceTransformer(settings.embedding_model)
            report(True, f"embedding model {settings.embedding_model} loads offline")
        except Exception as e:  # noqa: BLE001
            report(False, f"embedding model not cached ({type(e).__name__}); run `graphrag-gitlab setup-models`")
    report((settings.index_dir / "meta.json").exists(), f"index present in {settings.index_dir}")
    sys.exit(0 if ok else 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="graphrag-gitlab", description="Local GraphRAG assistant for GitLab repos")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-llm", action="store_true", help="retrieval-only mode (no LLM calls)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup-models", help="one-time download of the embedding model (needs internet)")
    sub.add_parser("doctor", help="check Ollama, models, privacy settings and index")

    p_ing = sub.add_parser("ingest", help="fetch repositories and build the graph + search index")
    p_ing.add_argument("--source", choices=["gitlab", "local"], default="gitlab")
    p_ing.add_argument("--path", action="append", default=[], help="local repo dir (repeatable, --source local)")
    p_ing.add_argument("--projects", help="comma-separated GitLab projects (overrides GITLAB_PROJECTS)")
    p_ing.add_argument("--max-projects", type=int, help="how many group projects to take when none are listed")

    p_ask = sub.add_parser("ask", help="ask one question")
    p_ask.add_argument("question")
    p_ask.add_argument("--repo", action="append", default=[], help="restrict to repository (repeatable)")
    p_ask.add_argument("--json", action="store_true", help="print the full result as JSON")

    sub.add_parser("chat", help="interactive question loop")
    sub.add_parser("stats", help="show index statistics")
    p_rel = sub.add_parser("related", help="show graph neighbours of a symbol")
    p_rel.add_argument("symbol")

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles with legacy code pages can't print → ◀
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    settings = Settings()
    if args.no_llm:
        settings.use_llm = False

    if args.cmd == "setup-models":
        return cmd_setup_models(settings)
    if args.cmd == "doctor":
        return cmd_doctor(settings)

    check_endpoints(settings)  # refuse to run against non-internal endpoints

    if args.cmd == "ingest":
        from .pipeline import ingest

        if args.projects:
            settings.gitlab_projects = [p.strip() for p in args.projects.split(",") if p.strip()]
        if args.max_projects:
            settings.gitlab_max_projects = args.max_projects
        index = ingest(settings, args.source, args.path)
        print(json.dumps(index.stats(), indent=2))

    elif args.cmd == "stats":
        from .index.store import CodeIndex

        print(json.dumps(CodeIndex.load(settings.index_dir).stats(), indent=2))

    elif args.cmd == "related":
        from .index.store import CodeIndex

        index = CodeIndex.load(settings.index_dir)
        ids = index.lookup_symbols([args.symbol])
        if not ids:
            raise SystemExit(f"No symbol named {args.symbol!r}")
        for cid in ids:
            ch = index.chunks[cid]
            print(f"{ch.kind} {ch.qualname}  ({ch.repo}/{ch.path}:{ch.start_line})")
            for direction, etype, other in index.relations(cid, limit=50):
                o = index.chunks.get(other)
                label = f"{o.qualname} ({o.repo}/{o.path}:{o.start_line})" if o else other
                print(f"   {'──' if direction == 'out' else '◀─'}{etype}{'─▶' if direction == 'out' else '──'} {label}")

    elif args.cmd == "ask":
        assistant = _assistant(settings)
        if args.json:
            result = assistant.ask(args.question, args.repo)
            result = {k: v for k, v in result.items() if k not in ("seeds", "context")}
            print(json.dumps(result, indent=2, default=str))
        else:
            _ask_and_print(assistant, args.question, args.repo, args.verbose)

    elif args.cmd == "chat":
        assistant = _assistant(settings)
        print(f"Indexed repositories: {', '.join(assistant.index.repos)}. Ctrl-C / Ctrl-D to exit.")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if q:
                _ask_and_print(assistant, q, [], args.verbose)


if __name__ == "__main__":
    main()
````

**File: `app.py`**

````python
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
````

Now install the project itself in editable mode, so the `graphrag-gitlab` command exists:
```powershell
pip install -e .
```

**Checkpoint 12:** `graphrag-gitlab --help` lists the commands.

---

## 17. Phase 13: Tests and sample repositories

Three tiny fake repositories (Python, Java, TypeScript) let you test everything without company code.
The tests need **neither Ollama nor network access**. `test_local_llm.py` starts a fake Ollama HTTP server on
localhost to exercise the real client code.

### 17.1 Sample repositories (fixtures)

**File: `tests/fixtures/notification-service/src/main/java/com/acme/notify/EmailSender.java`**

````java
package com.acme.notify;

import org.springframework.mail.javamail.JavaMailSender;

/**
 * Sends transactional email notifications to clients.
 */
public class EmailSender implements NotificationChannel {

    private final JavaMailSender mailSender;

    public EmailSender(JavaMailSender mailSender) {
        this.mailSender = mailSender;
    }

    @Override
    public void send(String recipient, String subject, String body) {
        String html = renderTemplate(subject, body);
        mailSender.send(buildMessage(recipient, html));
    }

    private String renderTemplate(String subject, String body) {
        return "<h1>" + subject + "</h1><p>" + body + "</p>";
    }

    private Object buildMessage(String recipient, String html) {
        return recipient + html;
    }
}
````

**File: `tests/fixtures/notification-service/src/main/java/com/acme/notify/NotificationChannel.java`**

````java
package com.acme.notify;

public interface NotificationChannel {
    void send(String recipient, String subject, String body);
}
````

**File: `tests/fixtures/notification-service/src/main/java/com/acme/notify/OrderEventListener.java`**

````java
package com.acme.notify;

import com.acme.notify.EmailSender;

/** Listens for order events and notifies the customer. */
public class OrderEventListener {
    private final EmailSender emailSender;

    public OrderEventListener(EmailSender emailSender) {
        this.emailSender = emailSender;
    }

    public void onOrderPlaced(String customerEmail, String orderId) {
        emailSender.send(customerEmail, "Order confirmed", "Your order " + orderId + " was placed.");
    }
}
````

**File: `tests/fixtures/order-service/README.md`**

````markdown
# Order Service
Handles order placement, payment capture and refunds for client accounts.

## Payments
Payments are captured through the PaymentGateway client.
````

**File: `tests/fixtures/order-service/orders/__init__.py`**

````python
(empty file)
````

**File: `tests/fixtures/order-service/orders/payments.py`**

````python
"""Payment gateway integration."""


class PaymentError(Exception):
    pass


class PaymentGateway:
    """Talks to the external card processor."""

    def charge(self, account_id: str, amount_cents: int) -> str:
        """Capture a payment and return the transaction id."""
        if amount_cents <= 0:
            raise PaymentError("amount must be positive")
        return f"txn-{account_id}-{amount_cents}"

    def refund(self, transaction_id: str) -> bool:
        """Reverse a captured payment."""
        return transaction_id.startswith("txn-")
````

**File: `tests/fixtures/order-service/orders/repository.py`**

````python
import sqlalchemy


class OrderRepository:
    """Persists orders in Postgres."""

    def __init__(self):
        self._rows = {}

    def save(self, order: dict) -> None:
        self._rows[order["txn"]] = order

    def get(self, order_id: str) -> dict:
        return self._rows[order_id]
````

**File: `tests/fixtures/order-service/orders/service.py`**

````python
from .payments import PaymentGateway, PaymentError
from .repository import OrderRepository


class OrderService:
    """Coordinates order placement: validation, payment, persistence."""

    def __init__(self):
        self.gateway = PaymentGateway()
        self.repo = OrderRepository()

    def place_order(self, account_id: str, items: list[dict]) -> dict:
        """Validate the cart, charge the customer and store the order."""
        total = calculate_total(items)
        txn = self.gateway.charge(account_id, total)
        order = {"account": account_id, "items": items, "total": total, "txn": txn}
        self.repo.save(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self.repo.get(order_id)
        return self.gateway.refund(order["txn"])


def calculate_total(items: list[dict]) -> int:
    """Sum item price * quantity in cents."""
    return sum(i["price_cents"] * i.get("qty", 1) for i in items)
````

**File: `tests/fixtures/web-portal/src/api/auth.ts`**

````typescript
import { httpClient } from "./http";

export interface Session {
  token: string;
  refreshToken: string;
}

/** Log the user in and return a session with access + refresh tokens. */
export async function login(username: string, password: string): Promise<Session> {
  const res = await httpClient.post("/auth/login", { username, password });
  return res.data as Session;
}

export const refreshSession = async (session: Session): Promise<Session> => {
  const res = await httpClient.post("/auth/refresh", { token: session.refreshToken });
  return res.data as Session;
};
````

**File: `tests/fixtures/web-portal/src/api/http.ts`**

````typescript
import axios from "axios";

export const httpClient = axios.create({ baseURL: "/api" });
````

### 17.2 Tests

**File: `tests/test_pipeline.py`**

````python
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
````

**File: `tests/test_local_llm.py`**

````python
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
````

**Checkpoint 13:**
```powershell
pytest -q
```
Expected: `17 passed`. Ignore the DeprecationWarnings that come from third-party packages.

What the tests prove:

| Test | Proves |
|---|---|
| `test_graph_structure` | CALLS, IMPORTS and INHERITS edges across Python, Java and TS; external deps detected; no fake external nodes for internal packages |
| `test_no_false_call_edges_for_builtin_methods` | `dict.get()` isn't linked to a project method; `self.repo.get()` is |
| `test_hybrid_search_finds_symbols` | BM25 + vector fusion finds the right symbols |
| `test_citation_url` | GitLab permalink format |
| `test_heuristic_intents` | The rule-based planner classifies the four question types |
| `test_agent_explain_expands_graph` | Graph expansion pulls the callees of `place_order` into the context |
| `test_agent_repos_intent` | Repository ranking |
| `test_agent_with_llm_cites_sources` | Streaming, citation flow, and removal of invented `[S99]` citations |
| `test_context_respects_budget` | The context stays within the configured size |
| `test_text_and_options` / `test_streaming` / `test_json_schema` | The real Ollama client sends `num_ctx`, streams and uses JSON schema |
| `test_missing_model` / `test_ollama_not_running` | Clear errors and fallback behaviour |
| `test_think_flag_only_sent_when_configured` | `think` is only sent for thinking models |
| `test_privacy_guard_rejects_public_llm_host` | Public IPs and `-cloud` models are refused; loopback and private IPs are allowed |
| `test_telemetry_locked_down` | Tracing and telemetry environment variables are forced off |

---

## 18. Phase 14: First run

```powershell
# 1. One-time: cache the embedding model (needs internet, downloads ~130 MB from huggingface.co, sends nothing)
graphrag-gitlab setup-models

# 2. Health check: every line should say [OK] except "index present" (no index yet)
graphrag-gitlab doctor

# 3. Index the sample repos
graphrag-gitlab ingest --source local --path tests/fixtures/order-service --path tests/fixtures/notification-service --path tests/fixtures/web-portal

# 4. Ask (first answer is slower while the model loads)
graphrag-gitlab -v ask "How does place_order work?"
graphrag-gitlab ask "Which repositories send email notifications?"
graphrag-gitlab related send

# 5. Web UI → opens http://localhost:8501
streamlit run app.py
```

Expected for step 4: the trace shows `intent=explain`, then `expand: +N graph neighbours`. The streamed answer
explains that `place_order` calls `calculate_total`, `PaymentGateway.charge` and `OrderRepository.save`, with
`[S#]` citations, followed by a **Sources** list.

**Checkpoint 14:** `doctor` is all `[OK]`, and the `ask` command produces a cited answer.

---

## 19. Phase 15: Index the real MS-ClientServices repositories

### 19.1 Create a read-only GitLab token

GitLab → your avatar → **Edit profile** → **Access tokens** → **Add new token**:
- Name: `graphrag-readonly`. Set a short expiry, for example 30 days.
- Scopes: **only** `read_api` and `read_repository`.

Copy the token into `.env`. Never commit it.

### 19.2 Fill in `.env`

```dotenv
GITLAB_URL=https://gitlab.your-company.internal
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxx
GITLAB_GROUP=MS-ClientServices          # use the full path if it's a subgroup, e.g. clients/MS-ClientServices
GITLAB_PROJECTS=repo-one,repo-two,repo-three
```

Pick 3–5 small-to-medium repositories for the prototype. Ingestion time grows with code size, mostly from
embedding on the CPU (roughly 1–3 minutes per 1,000 functions).

If your GitLab uses an internal certificate authority, point Python at the company CA bundle rather than disabling SSL:
```powershell
setx REQUESTS_CA_BUNDLE C:\path\to\company-ca-bundle.pem
```

### 19.3 Ingest and ask

```powershell
graphrag-gitlab -v ingest
graphrag-gitlab stats
graphrag-gitlab chat
```

Re-run `graphrag-gitlab ingest` whenever you want to pick up new commits. It rebuilds the index from scratch.

**Checkpoint 15:** `stats` lists your repositories, and the citations in answers open the right lines in GitLab.

---

## 20. Phase 16: Prove that nothing leaves the machine

Do this after ingestion. It blocks **all** outbound internet traffic for Python and Ollama, then shows that the
assistant still works. Local traffic between the app and Ollama on `127.0.0.1` isn't affected by these rules.

### 20.1 Windows (PowerShell **as Administrator**, in the project folder)

```powershell
# Find the real interpreter behind the venv
$py = & .venv\Scripts\python.exe -c "import sys; print(sys._base_executable)"
$venvPy = (Resolve-Path .venv\Scripts\python.exe).Path
$ollamaDir = "$env:LOCALAPPDATA\Programs\Ollama"

New-NetFirewallRule -DisplayName "GraphRAG-Block-Python"     -Direction Outbound -Program $py     -Action Block
New-NetFirewallRule -DisplayName "GraphRAG-Block-VenvPython" -Direction Outbound -Program $venvPy -Action Block
New-NetFirewallRule -DisplayName "GraphRAG-Block-Ollama"     -Direction Outbound -Program "$ollamaDir\ollama.exe" -Action Block
New-NetFirewallRule -DisplayName "GraphRAG-Block-OllamaApp"  -Direction Outbound -Program "$ollamaDir\ollama app.exe" -Action Block
```

Now test, in a normal PowerShell with the venv activated:
```powershell
graphrag-gitlab doctor
graphrag-gitlab ask "How does place_order work?"
streamlit run app.py
```
Everything must still work. Expected exceptions: `ingest` from GitLab fails, which is expected and proves the block
works, and Ollama can no longer check for updates.

Optional live monitoring: open **Resource Monitor** → **Network** → **TCP Connections** while asking a question.
`python.exe` and `ollama.exe` should only show connections to `127.0.0.1`.

To allow ingestion again, remove the Python rules (keep the Ollama rules permanently if you like and update Ollama manually):
```powershell
Remove-NetFirewallRule -DisplayName "GraphRAG-Block-Python","GraphRAG-Block-VenvPython"
```

### 20.2 Linux

```bash
sudo ufw default deny outgoing && sudo ufw enable        # loopback stays allowed
graphrag-gitlab ask "How does place_order work?"         # must still work
sudo ss -tnp | grep -E "python|ollama"                   # only 127.0.0.1 connections
sudo ufw default allow outgoing                          # restore
```

### 20.3 Checklist for your security team

- [ ] Ollama listens on `127.0.0.1:11434` only (`netstat`)
- [ ] No `-cloud` models pulled (`ollama list`); not signed in to an ollama.com account
- [ ] `graphrag-gitlab doctor` shows `[OK]` for the endpoint, telemetry and offline-mode checks
- [ ] Streamlit bound to `127.0.0.1` (`.streamlit/config.toml`), no usage stats
- [ ] GitLab token is read-only, short-lived and stored only in `.env` (git-ignored)
- [ ] BitLocker enabled; `data/` excluded from backups and sync tools that leave the company (OneDrive etc.) if policy requires
- [ ] Firewall test (20.1) passed: the assistant answers with outbound traffic blocked
- [ ] Packages installed from PyPI or an approved internal mirror; model from ollama.com; licences approved

---

## 21. Performance tuning for a CPU-only laptop

Edit `.env`. Changes apply the next time you run a command or restart the UI.

| Knob | Default | Faster | Effect |
|---|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | `qwen3:4b` + `OLLAMA_THINK=false` | Roughly 2x faster; slightly weaker explanations |
| `MAX_CONTEXT_CHUNKS` | `6` | `4` | Less prompt to read, so a faster first word |
| `MAX_SNIPPET_LINES` | `40` | `25` | Same |
| `NUM_CTX` | `8192` | `4096` | Less RAM and faster; the budget logic keeps the prompt inside it |
| `USE_LLM_PLANNER` | `false` | keep `false` | `true` adds a whole extra LLM call |
| `OLLAMA_KEEP_ALIVE` | `30m` | `2h` | Avoids reloading the model (about 10–20 s) |
| `--no-llm` | | | Instant: ranked locations + links only |

Also: close other memory-heavy apps, keep the laptop plugged in (battery mode throttles the CPU), and use the
*Best performance* power mode in Windows.

---

## 22. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Cannot reach Ollama at http://127.0.0.1:11434` | Ollama isn't running. Start it from the Start menu (Windows) or `sudo systemctl start ollama`. The answer falls back to retrieval-only meanwhile |
| `Model 'qwen2.5-coder:7b' is not pulled` | `ollama pull qwen2.5-coder:7b` |
| `We couldn't connect to 'https://huggingface.co'` | The embedding model isn't cached yet. Run `graphrag-gitlab setup-models` once (online) |
| Warning `Could not reach the Hub … Using cached commit hash` | Harmless. Offline mode is working as intended |
| `OLLAMA_HOST_URL=… is not a local/private address` | The privacy guard is working. For an approved internal server, add its hostname to `ALLOWED_HOSTS` |
| `… is an Ollama cloud model` | Choose a local model |
| Answers very slow or the laptop swaps | See section 21. Try `qwen3:4b`, `NUM_CTX=4096`, `MAX_CONTEXT_CHUNKS=4`, and close apps |
| `graphrag-gitlab` not recognised | The venv isn't activated. Run `.venv\Scripts\Activate.ps1`, or use `python -m graphrag_gitlab.cli …` |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or use `cmd` and `.venv\Scripts\activate.bat` |
| GitLab `401` / `403` | Token expired or missing the `read_api` / `read_repository` scopes, or no access to the project |
| GitLab `404 Group Not Found` | `GITLAB_GROUP` must be the full path, e.g. `parent-group/MS-ClientServices` |
| SSL certificate errors to GitLab | Set `REQUESTS_CA_BUNDLE` to the company CA bundle (19.2) |
| `Projects not found in group` warning | A name in `GITLAB_PROJECTS` doesn't match. Use the project path as shown in its URL |
| `Index was built with … embeddings` | You changed `EMBEDDING_BACKEND`. Re-run `ingest` |
| Answer cites the wrong code or misses something | Run with `-v` to see the trace. Increase `MAX_CONTEXT_CHUNKS`, mention the function name in backticks, or restrict with `--repo` |
| `pip install` fails behind the proxy | `pip config set global.index-url <internal mirror>` or `--proxy` |

---

## 23. Next steps after the prototype

1. **Shared internal server.** Run Ollama on one internal GPU machine (16–24 GB VRAM) with a 14B–32B model
   for much better answers. Set `OLLAMA_HOST_URL=http://<server>:11434` and `ALLOWED_HOSTS=<server>`.
   Protect that server with a firewall or reverse proxy, because Ollama has no authentication of its own.
2. **Better parsing.** Replace the regex parser with tree-sitter for exact ASTs in every language.
3. **Cross-repository edges.** Link HTTP clients to the services that implement them (OpenAPI specs, route definitions), and shared libraries to their users.
4. **Incremental ingestion.** Re-index only projects whose commit sha changed, triggered by GitLab webhooks.
5. **Graph and vector stores at scale.** Neo4j or Memgraph for the graph (same schema), and Qdrant or pgvector for the vectors,
   all self-hosted.
6. **Access control.** Filter results by the asking user's GitLab project permissions.
7. **Evaluation set.** 30–50 real questions with known answers, to compare models and settings objectively.

---

## Appendix A: Extract all files automatically

Instead of creating each file by hand, save this script as `extract_files.py` **next to** this guide and run
`python extract_files.py IMPLEMENTATION_GUIDE.md C:\dev\gitlab-graphrag`. It writes every
``File: `path` `` block in this guide to disk. Review the output before running anything.

````python
"""Write every 'File: `path`' code block from IMPLEMENTATION_GUIDE.md to disk."""
import re
import sys
from pathlib import Path

guide, target = Path(sys.argv[1]), Path(sys.argv[2] if len(sys.argv) > 2 else ".")
text = guide.read_text(encoding="utf-8")
pattern = re.compile(r"^\*\*File: `([^`]+)`\*\*\n\n(`{4,})[^\n]*\n(.*?)\n\2$", re.MULTILINE | re.DOTALL)
count = 0
for path, _fence, body in pattern.findall(text):
    if ".." in Path(path).parts or Path(path).is_absolute():
        raise SystemExit(f"refusing unsafe path {path}")
    out = target / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("" if body == "(empty file)" else body + "\n", encoding="utf-8", newline="\n")
    count += 1
    print("wrote", out)
print(f"{count} files written to {target.resolve()}")
````

After extracting, continue from Phase 3 (7.3) to create the venv and install the dependencies.

---

## Appendix B: Tested versions

Verified on 2026-11-27 (all 17 tests passing):

| Package | Version |
|---|---|
| langgraph | 1.2.24 |
| networkx | 3.7 |
| numpy | 2.5.5 |
| rank-bm25 | 0.2.2 |
| python-gitlab | 8.6.0 |
| python-dotenv | 1.2.4 |
| ollama (Python client) | 0.6.3 |
| httpx | 0.29.0 |
| sentence-transformers | 6.3.0 |
| torch | 2.15.0 |
| streamlit | 1.67.0 |
| pytest | 9.2.0 |
| Embedding model | BAAI/bge-small-en-v1.5 |
| LLM | qwen2.5-coder:7b (Ollama) |

`requirements.txt` uses minimum versions (`>=`). If a future release breaks something, pin these exact
versions with `pip install "langgraph==1.2.24" …`.
