"""Domain Lead: a code-mutating agent that works one directive in the sandbox.

Runs on the managed-agent path (persistent Linux sandbox + code_execution). It
edits only its domain's files, iterates with pytest until green inside a single
fat interaction, and reports a structured result — including any cross-boundary
contract changes, which it sends back to the Architect (never to a peer).
"""
from __future__ import annotations

from ..protocol.models import ContractChange, Domain, DomainDirective, LeadReport
from .architect import _extract_json
from .backend import AgentBackend

LEAD_SYSTEM = """\
You are a Domain Lead engineer agent in a multi-agent coding swarm.
You own the domain '{domain}', consisting of these Python modules: {modules}.
The repository is checked out at {repo_dir} in your sandbox.{pkg_hint}

Rules:
- Modify ONLY files belonging to your domain's modules. Never touch other domains' files.
- Use the shell to edit code and run pytest. Run only the tests relevant to your
  change (target the affected test files/paths) so iterations stay fast — you do
  not need to run the entire suite.
- Iterate until the tests you can run pass (self-heal); do the work, don't just plan it.
- If you change the PUBLIC signature of a function/class that other modules import,
  you MUST report it as a contract change so the Architect can propagate it.

When finished, respond with ONLY this JSON (no prose, no code fences):
{{"success": true, "summary": "what you did", "tests_passed": true,
  "contract_changes": [
    {{"target_module": "a.b", "target_symbol": "fn", "proposed_signature": "fn(x: int) -> str", "reason": "why"}}
  ]}}
"""


class DomainLead:
    def __init__(self, backend: AgentBackend, domain: Domain,
                 repo_dir: str = "/workspace/repo", package_dir: str = ""):
        self.backend = backend
        self.domain = domain
        self.repo_dir = repo_dir
        # Absolute path in the sandbox where the package's modules live, e.g.
        # "/workspace/repo/fastapi" — lets the Lead map dotted names to files.
        self.package_dir = package_dir or repo_dir

    def execute(self, directive: DomainDirective, environment_id: str) -> tuple[LeadReport, str]:
        """Run one directive in the shared sandbox. Returns (report, interaction_id)."""
        pkg_hint = (
            f"\nYour modules are dotted paths under {self.package_dir} "
            f"(e.g. module 'a.b' is the file {self.package_dir}/a/b.py)."
        )
        system = LEAD_SYSTEM.format(
            domain=self.domain.name,
            modules=", ".join(self.domain.module_names),
            repo_dir=self.repo_dir,
            pkg_hint=pkg_hint,
        )
        # A fresh conversation chain per directive, but the SHARED environment id
        # (so all edits land in one workspace — safe because v0 is serialized).
        turn = self.backend.start(system, directive.instruction, environment=environment_id)
        return self._parse(turn.text), turn.interaction_id

    def _parse(self, text: str) -> LeadReport:
        try:
            data = _extract_json(text)
        except ValueError:
            return LeadReport(
                domain=self.domain.name, success=False,
                summary="could not parse agent report", tests_output=text[:500])

        changes = [
            ContractChange(
                target_module=c.get("target_module", ""),
                target_symbol=c.get("target_symbol", ""),
                proposed_signature=c.get("proposed_signature", ""),
                reason=c.get("reason", ""),
            )
            for c in data.get("contract_changes", []) or []
        ]
        return LeadReport(
            domain=self.domain.name,
            success=bool(data.get("success", False)),
            summary=data.get("summary", ""),
            tests_passed=bool(data.get("tests_passed", False)),
            contract_changes=changes,
        )
