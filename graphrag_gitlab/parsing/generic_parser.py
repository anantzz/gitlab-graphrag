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
