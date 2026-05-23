"""Offline test of the serialized orchestrator: a contract change in one domain
routes a propagation directive to the dependent domain (FakeReasoner + FakeBackend)."""
from __future__ import annotations

from pathlib import Path

from swarm.agents.architect import Architect
from swarm.agents.backend import FakeBackend, FakeReasoner
from swarm.mapping.ast_mapper import map_repository
from swarm.orchestrator import Orchestrator
from swarm.protocol.models import Domain


def _repo(base: Path) -> None:
    (base / "pkg").mkdir()
    (base / "pkg" / "__init__.py").write_text("")
    (base / "pkg" / "auth.py").write_text("def login(u: str) -> bool:\n    return True\n")
    (base / "pkg" / "api.py").write_text("from .auth import login\ndef route():\n    return login('x')\n")


def test_serialized_run_propagates_contract_change(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    domains = [Domain("auth", ["pkg.auth"]), Domain("web", ["pkg.api"])]

    # Architect plans one directive (for auth) via the reasoning path.
    reasoner = FakeReasoner(responses=[
        '{"directives": [{"domain": "auth", "instruction": "add scopes to login", '
        '"target_modules": ["pkg.auth"]}]}'
    ])
    # Sandbox turns, in order: bootstrap, auth lead (emits a contract change), web lead.
    backend = FakeBackend(responses=[
        '{"baseline_tests_passed": true, "summary": "cloned, 10 passed"}',
        '{"success": true, "summary": "added scopes", "tests_passed": true, '
        '"contract_changes": [{"target_module": "pkg.auth", "target_symbol": "login", '
        '"proposed_signature": "login(u, scopes)", "reason": "added scopes"}]}',
        '{"success": true, "summary": "updated caller in pkg.api", "tests_passed": true, '
        '"contract_changes": []}',
    ])

    orch = Orchestrator(Architect(reasoner), backend, repo_url="https://example.com/r.git")
    result = orch.run(topo, domains, intent="add scope-based authorization to login")

    assert result.environment_id == "fake-env-0001"
    assert [r.domain for r in result.reports] == ["auth", "web"]   # web added by propagation
    assert result.reports[0].contract_changes[0].target_symbol == "login"
    assert result.reports[1].domain == "web" and result.reports[1].tests_passed
    # The propagation directive (3rd sandbox prompt) mentions the changed signature.
    assert "login(u, scopes)" in backend.prompts[-1]


def test_run_with_no_contract_changes_does_not_propagate(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    domains = [Domain("auth", ["pkg.auth"]), Domain("web", ["pkg.api"])]
    reasoner = FakeReasoner(responses=[
        '{"directives": [{"domain": "auth", "instruction": "tidy login", "target_modules": ["pkg.auth"]}]}'
    ])
    backend = FakeBackend(responses=[
        '{"baseline_tests_passed": true, "summary": "ok"}',
        '{"success": true, "summary": "renamed a local var", "tests_passed": true, "contract_changes": []}',
    ])
    result = Orchestrator(Architect(reasoner), backend, repo_url="x").run(topo, domains, "tidy up")
    assert [r.domain for r in result.reports] == ["auth"]  # no propagation
