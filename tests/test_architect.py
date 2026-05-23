"""Offline tests for the Architect: segmentation, planning, propagation (FakeReasoner)."""
from __future__ import annotations

from pathlib import Path

import pytest

from swarm.agents.architect import Architect, _extract_json
from swarm.agents.backend import FakeReasoner
from swarm.mapping.ast_mapper import map_repository
from swarm.mapping.partition import partition_modules
from swarm.protocol.models import ContractChange, Domain, LeadReport


def _repo(base: Path) -> None:
    (base / "pkg").mkdir()
    (base / "pkg" / "__init__.py").write_text("")
    (base / "pkg" / "auth.py").write_text("def login(u: str) -> bool:\n    return True\n")
    (base / "pkg" / "api.py").write_text("from .auth import login\ndef route():\n    return login('x')\n")


def _two_group_repo(base: Path) -> None:
    """Two dependency-disjoint groups: {a,b} and {x,y}."""
    (base / "pkg").mkdir()
    (base / "pkg" / "__init__.py").write_text("")
    (base / "pkg" / "a.py").write_text("def fa() -> int:\n    return 1\n")
    (base / "pkg" / "b.py").write_text("from .a import fa\ndef fb():\n    return fa()\n")
    (base / "pkg" / "x.py").write_text("def fx() -> int:\n    return 1\n")
    (base / "pkg" / "y.py").write_text("from .x import fx\ndef fy():\n    return fx()\n")


# --- JSON extraction -------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"domains": []}') == {"domains": []}


def test_extract_json_with_prose_and_fence():
    text = 'Sure, here is the plan:\n```json\n{"domains": [{"name": "x"}]}\n```\nDone.'
    assert _extract_json(text) == {"domains": [{"name": "x"}]}


def test_extract_json_embedded_braces_in_strings():
    text = '{"domains": [{"name": "a", "rationale": "uses {curly} braces"}]}'
    assert _extract_json(text)["domains"][0]["rationale"] == "uses {curly} braces"


# --- Segmentation: hybrid (deterministic partition + LLM naming) -----------

def test_partition_is_deterministic_and_separates_groups(tmp_path: Path):
    _two_group_repo(tmp_path)
    topo = map_repository(tmp_path)
    c1 = partition_modules(topo)
    c2 = partition_modules(topo)
    assert c1 == c2  # seeded → reproducible
    groups = [set(c) for c in c1]
    assert {"pkg.a", "pkg.b"} in groups
    assert {"pkg.x", "pkg.y"} in groups


def test_segment_hybrid_names_algorithmic_clusters(tmp_path: Path):
    _two_group_repo(tmp_path)
    topo = map_repository(tmp_path)
    reasoner = FakeReasoner(responses=[
        '{"domains": [{"name": "alpha", "rationale": "a/b group"}, '
        '{"name": "beta", "rationale": "x/y group"}]}'
    ])
    seg = Architect(reasoner).segment(topo)  # hybrid is the default
    assert sorted(d.name for d in seg.domains) == ["alpha", "beta"]
    # the module grouping comes from the deterministic partition, not the LLM
    grouping = sorted(tuple(sorted(d.module_names)) for d in seg.domains)
    assert grouping == [("pkg.a", "pkg.b"), ("pkg.x", "pkg.y")]


def test_segment_hybrid_autonames_when_naming_fails(tmp_path: Path):
    _two_group_repo(tmp_path)
    topo = map_repository(tmp_path)
    seg = Architect(FakeReasoner(responses=["sorry, no json here"])).segment(topo)
    assert len(seg.domains) == 2
    assert all(d.name for d in seg.domains)          # deterministic fallback names
    assert len({d.name for d in seg.domains}) == 2   # and unique


# --- Segmentation: pure-LLM path (use_llm_partition=True) ------------------

def test_llm_segment_assigns_and_validates(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    reasoner = FakeReasoner(responses=[
        '{"domains": ['
        '{"name": "auth", "module_names": ["pkg.auth"], "rationale": "authentication"},'
        '{"name": "web", "module_names": ["pkg.api", "pkg.nonexistent"], "rationale": "routing"}'
        ']}'
    ])
    result = Architect(reasoner).segment(topo, use_llm_partition=True)
    names = {d.name: d for d in result.domains}
    assert names["auth"].module_names == ["pkg.auth"]
    assert names["web"].module_names == ["pkg.api"]  # unknown module dropped
    assert result.unassigned == []


def test_llm_segment_reports_unassigned(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    reasoner = FakeReasoner(responses=[
        '{"domains": [{"name": "auth", "module_names": ["pkg.auth"], "rationale": "x"}]}'
    ])
    result = Architect(reasoner).segment(topo, use_llm_partition=True)
    assert "pkg.api" in result.unassigned


def test_llm_segment_raises_on_no_json(tmp_path: Path):
    _repo(tmp_path)
    topo = map_repository(tmp_path)
    with pytest.raises(ValueError):
        Architect(FakeReasoner(responses=["I could not complete the task."])).segment(
            topo, use_llm_partition=True)


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
