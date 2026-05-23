"""Offline tests for the Domain Lead's report parsing (FakeBackend)."""
from __future__ import annotations

from swarm.agents.backend import FakeBackend
from swarm.agents.lead import DomainLead
from swarm.protocol.models import Domain, DomainDirective


def _lead(responses):
    return DomainLead(FakeBackend(responses=responses), Domain("auth", ["pkg.auth"]))


def test_parses_success_and_contract_changes():
    lead = _lead([
        '{"success": true, "summary": "added scopes", "tests_passed": true, '
        '"contract_changes": [{"target_module": "pkg.auth", "target_symbol": "login", '
        '"proposed_signature": "login(u, scopes)", "reason": "added scopes"}]}'
    ])
    report, interaction_id = lead.execute(DomainDirective("auth", "add scopes"), "env-1")
    assert report.success and report.tests_passed
    assert report.domain == "auth"
    assert len(report.contract_changes) == 1
    assert report.contract_changes[0].target_symbol == "login"
    assert interaction_id  # backend returns a handle for resumption


def test_unparseable_report_is_a_clean_failure():
    report, _ = _lead(["I tried but ran out of time."]).execute(
        DomainDirective("auth", "do it"), "env-1")
    assert report.success is False
    assert "could not parse" in report.summary


def test_missing_contract_changes_defaults_empty():
    report, _ = _lead(['{"success": true, "summary": "done", "tests_passed": true}']).execute(
        DomainDirective("auth", "do it"), "env-1")
    assert report.contract_changes == []
