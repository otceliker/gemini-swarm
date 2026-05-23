"""Offline tests for the Architect: segmentation, planning, propagation (FakeReasoner)."""
from __future__ import annotations

from pathlib import Path

import pytest

from swarm.agents.architect import Architect, _extract_json
from swarm.agents.backend import FakeReasoner
from swarm.mapping.ast_mapper import map_repository
from swarm.protocol.models import ContractChange, Domain, LeadReport


def _repo(base: Path) -> None:
    (base / "pkg").mkdir()
    (base / "pkg" / "__init__.py").write_text("")
    (base / "pkg" / "auth.py").write_text("def login(u: str) -> bool:\n    return True\n")
    (base / "pkg" / "api.py").write_text("from .auth import login\ndef route():\n    return login('x')\n")


# --- JSON extraction -------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"domains": []}') == {"domains": []}


def test_extract_json_with_prose_and_fence():
    text = 'Sure, here is the plan:\n```json\n{"domains": [{"name": "x"}]}\n```\nDone.'
    assert _extract_json(text) == {"domains": [{"name": "x"}]}


def test_extract_json_embedded_braces_in_strings():
    text = '{"domains": [{"name": "a", "rationale": "uses {curly} braces"}]}'
    assert _extract_json(text)["domains"][0]["rationale"] == "uses {curly} braces"


# --- Segmentation ----------------------------------------------------------

def test_segment_assigns_and_validates(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    reasoner = FakeReasoner(responses=[
        '{"domains": ['
        '{"name": "auth", "module_names": ["pkg.auth"], "rationale": "authentication"},'
        '{"name": "web", "module_names": ["pkg.api", "pkg.nonexistent"], "rationale": "routing"}'
        ']}'
    ])
    result = Architect(reasoner).segment(topo)
    names = {d.name: d for d in result.domains}
    assert names["auth"].module_names == ["pkg.auth"]
    assert names["web"].module_names == ["pkg.api"]  # unknown module dropped
    assert result.unassigned == []


def test_segment_reports_unassigned(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    reasoner = FakeReasoner(responses=[
        '{"domains": [{"name": "auth", "module_names": ["pkg.auth"], "rationale": "x"}]}'
    ])
    result = Architect(reasoner).segment(topo)
    assert "pkg.api" in result.unassigned


def test_segment_raises_on_no_json(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    with pytest.raises(ValueError):
        Architect(FakeReasoner(responses=["I could not complete the task."])).segment(topo)


# --- Planning --------------------------------------------------------------

def test_plan_filters_unknown_domains_and_modules(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    domains = [Domain("auth", ["pkg.auth"]), Domain("web", ["pkg.api"])]
    reasoner = FakeReasoner(responses=[
        '{"directives": ['
        '{"domain": "auth", "instruction": "add scopes to login", "target_modules": ["pkg.auth"]},'
        '{"domain": "ghost", "instruction": "do nothing", "target_modules": ["pkg.api"]},'
        '{"domain": "web", "instruction": "x", "target_modules": ["pkg.api", "pkg.bogus"]}'
        ']}'
    ])
    directives = Architect(reasoner).plan(topo, "add scopes", domains)
    by_domain = {d.domain: d for d in directives}
    assert set(by_domain) == {"auth", "web"}          # "ghost" dropped
    assert by_domain["web"].target_modules == ["pkg.api"]  # "pkg.bogus" dropped
    assert by_domain["auth"].kind == "primary"


# --- Propagation (pure graph logic + cycle guard) --------------------------

def test_resolve_propagations_routes_to_dependent_domain(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    domains = [Domain("auth", ["pkg.auth"]), Domain("web", ["pkg.api"])]
    report = LeadReport(domain="auth", success=True, contract_changes=[
        ContractChange("pkg.auth", "login", "login(u: str, scopes: list) -> bool", "added scopes")
    ])
    seen: set = set()
    arch = Architect(FakeReasoner())
    props = arch.resolve_propagations(topo, domains, report, seen)
    assert len(props) == 1
    assert props[0].domain == "web"            # pkg.api imports pkg.auth -> web must adapt
    assert props[0].kind == "propagation"
    assert "pkg.api" in props[0].target_modules

    # Cycle guard: the same change does not propagate twice.
    assert arch.resolve_propagations(topo, domains, report, seen) == []


def test_resolve_propagations_ignores_same_domain(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    # Both modules in one domain -> a contract change has no *other* domain to notify.
    domains = [Domain("all", ["pkg.auth", "pkg.api"])]
    report = LeadReport(domain="all", success=True, contract_changes=[
        ContractChange("pkg.auth", "login", "login() -> bool", "x")
    ])
    assert Architect(FakeReasoner()).resolve_propagations(topo, domains, report, set()) == []
