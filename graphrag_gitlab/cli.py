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
