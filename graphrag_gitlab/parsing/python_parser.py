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
