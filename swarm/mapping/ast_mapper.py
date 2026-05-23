"""Deterministic repository mapping via the stdlib `ast` module.

This is Phase 1 of the pipeline and intentionally contains no LLM calls: given a
checked-out Python repo, it produces a `Topology` (and `topology.json`) describing
modules, their public symbols/signatures, and the *internal* dependency edges
between them. The Architect later segments this graph into domains.

Python-only by design for v0.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from ..protocol.models import Module, Symbol, Topology

# Directories we never treat as source.
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
              "node_modules", "build", "dist", ".tox", ".eggs"}


def _module_name_from_path(rel_path: Path) -> str:
    """`auth/service.py` -> `auth.service`; `auth/__init__.py` -> `auth`."""
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _signature(node: ast.AST, name: str) -> str:
    """Render a readable signature using ast.unparse (stdlib, 3.9+)."""
    try:
        args = ast.unparse(node.args)  # type: ignore[attr-defined]
    except Exception:
        args = ""
    ret = ""
    returns = getattr(node, "returns", None)
    if returns is not None:
        try:
            ret = f" -> {ast.unparse(returns)}"
        except Exception:
            ret = ""
    return f"{name}({args}){ret}"


def _resolve_relative(module_name: str, is_pkg_init: bool, level: int, sub: str | None) -> str:
    """Resolve a relative import target to a dotted module name.

    `from ..auth.service import x` inside `pkg.routers.login` -> `pkg.auth.service`.
    """
    parts = module_name.split(".") if module_name else []
    base = parts if is_pkg_init else parts[:-1]
    # Each extra level beyond 1 walks one package upward.
    up = level - 1
    if up > 0:
        base = base[:-up] if up <= len(base) else []
    if sub:
        base = base + sub.split(".")
    return ".".join(base)


def _collect_imports(tree: ast.AST, module_name: str, is_pkg_init: bool) -> list[str]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                targets.append(_resolve_relative(module_name, is_pkg_init, node.level, node.module))
            elif node.module:
                targets.append(node.module)
    return targets


def _collect_symbols(tree: ast.Module, exports: set[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(Symbol(
                name=node.name, kind="function",
                signature=_signature(node, node.name), lineno=node.lineno,
                is_public=_is_public(node.name, exports),
            ))
        elif isinstance(node, ast.ClassDef):
            symbols.append(Symbol(
                name=node.name, kind="class",
                signature=f"class {node.name}", lineno=node.lineno,
                is_public=_is_public(node.name, exports),
            ))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qname = f"{node.name}.{child.name}"
                    symbols.append(Symbol(
                        name=qname, kind="method",
                        signature=_signature(child, qname), lineno=child.lineno,
                        is_public=_is_public(node.name, exports) and not child.name.startswith("_"),
                    ))
    return symbols


def _is_public(name: str, exports: set[str]) -> bool:
    if exports:
        return name in exports
    return not name.startswith("_")


def _explicit_exports(tree: ast.Module) -> set[str]:
    """Read `__all__ = [...]` if present."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return {
                            el.value for el in node.value.elts
                            if isinstance(el, ast.Constant) and isinstance(el.value, str)
                        }
    return set()


def map_repository(root: str | Path) -> Topology:
    """Walk `root`, parse every .py file, and build the dependency topology."""
    root = Path(root).resolve()
    py_files: list[Path] = [
        p for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    ]

    modules: list[Module] = []
    for path in sorted(py_files):
        rel = path.relative_to(root)
        module_name = _module_name_from_path(rel)
        is_pkg_init = rel.name == "__init__.py"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            # Unparseable file: record it as an empty module rather than crashing.
            modules.append(Module(path=rel.as_posix(), module_name=module_name))
            continue

        exports = _explicit_exports(tree)
        symbols = _collect_symbols(tree, exports)
        imports = _collect_imports(tree, module_name, is_pkg_init)
        export_names = sorted(exports) if exports else [s.name for s in symbols if s.is_public]
        modules.append(Module(
            path=rel.as_posix(), module_name=module_name,
            symbols=symbols, imports=sorted(set(imports)), exports=export_names,
        ))

    _link_internal_deps(modules)
    return Topology(root=str(root), modules=modules)


def _link_internal_deps(modules: list[Module]) -> None:
    """Keep only imports that resolve to another module in this repo."""
    internal = {m.module_name for m in modules}
    for m in modules:
        deps: set[str] = set()
        for target in m.imports:
            if not target:
                continue
            if target in internal and target != m.module_name:
                deps.add(target)
                continue
            # `import pkg.sub` may reference a package; link to its members.
            for name in internal:
                if name != m.module_name and name.startswith(target + "."):
                    deps.add(name)
        m.internal_deps = sorted(deps)


def write_topology(topology: Topology, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(json.dumps(topology.to_dict(), indent=2), encoding="utf-8")
    return out_path
