"""Tests for the deterministic AST mapper."""
from __future__ import annotations

from pathlib import Path

from swarm.mapping.ast_mapper import map_repository, write_topology
from swarm.protocol.models import Topology


def _write(base: Path, rel: str, content: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_repo(base: Path) -> None:
    _write(base, "pkg/__init__.py", "")
    _write(base, "pkg/auth/__init__.py", "")
    _write(base, "pkg/auth/service.py", (
        "import os\n"
        "def verify_jwt_token(token: str, required_scopes: list[str]) -> bool:\n"
        "    return True\n"
        "def _private_helper():\n"
        "    return 1\n"
    ))
    _write(base, "pkg/routers/__init__.py", "")
    _write(base, "pkg/routers/login.py", (
        "from ..auth.service import verify_jwt_token\n"
        "class LoginRouter:\n"
        "    def handle(self, token: str) -> bool:\n"
        "        return verify_jwt_token(token, [])\n"
        "    def _internal(self):\n"
        "        return None\n"
    ))


def test_discovers_modules_and_names(tmp_path: Path):
    _make_repo(tmp_path)
    topo = map_repository(tmp_path)
    names = set(topo.module_names())
    assert {"pkg", "pkg.auth", "pkg.auth.service", "pkg.routers", "pkg.routers.login"} <= names


def test_internal_dependency_edge_from_relative_import(tmp_path: Path):
    _make_repo(tmp_path)
    topo = map_repository(tmp_path)
    login = next(m for m in topo.modules if m.module_name == "pkg.routers.login")
    # The relative `from ..auth.service import ...` must resolve to the internal module.
    assert "pkg.auth.service" in login.internal_deps
    # `import os` is third-party/stdlib and must NOT appear as an internal dep.
    service = next(m for m in topo.modules if m.module_name == "pkg.auth.service")
    assert service.internal_deps == []


def test_signatures_and_public_surface(tmp_path: Path):
    _make_repo(tmp_path)
    topo = map_repository(tmp_path)
    service = next(m for m in topo.modules if m.module_name == "pkg.auth.service")
    sig = {s.name: s for s in service.symbols}
    assert "verify_jwt_token" in sig
    assert "token: str" in sig["verify_jwt_token"].signature
    assert "required_scopes: list[str]" in sig["verify_jwt_token"].signature
    assert "-> bool" in sig["verify_jwt_token"].signature
    assert sig["verify_jwt_token"].is_public is True
    assert sig["_private_helper"].is_public is False
    assert "verify_jwt_token" in service.exports
    assert "_private_helper" not in service.exports


def test_class_methods_extracted(tmp_path: Path):
    _make_repo(tmp_path)
    topo = map_repository(tmp_path)
    login = next(m for m in topo.modules if m.module_name == "pkg.routers.login")
    kinds = {s.name: s.kind for s in login.symbols}
    assert kinds.get("LoginRouter") == "class"
    assert kinds.get("LoginRouter.handle") == "method"


def test_syntax_error_does_not_crash(tmp_path: Path):
    _make_repo(tmp_path)
    _write(tmp_path, "pkg/broken.py", "def oops(:\n")  # invalid syntax
    topo = map_repository(tmp_path)  # must not raise
    broken = next(m for m in topo.modules if m.module_name == "pkg.broken")
    assert broken.symbols == []


def test_roundtrip_serialization(tmp_path: Path):
    _make_repo(tmp_path)
    topo = map_repository(tmp_path)
    out = write_topology(topo, tmp_path / "topology.json")
    assert out.exists()
    restored = Topology.from_dict(__import__("json").loads(out.read_text()))
    assert set(restored.module_names()) == set(topo.module_names())
