"""Serialized swarm orchestrator (v0).

Ties the pieces together for autonomous modification:
  plan (Architect) -> bootstrap one shared sandbox -> run directives one at a
  time, routing every cross-boundary contract change back through the Architect
  for propagation, with a global cycle guard + directive budget.

Serialized + single shared sandbox = no concurrent edits, so no merge problem.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .agents.architect import Architect
from .agents.backend import AgentBackend
from .agents.lead import DomainLead
from .protocol.models import Domain, DomainDirective, LeadReport, Topology

BOOT_SYSTEM = (
    "You are the workspace bootstrapper for a coding swarm. Use the shell to set up "
    "the repository, then report status as JSON only."
)
# Hard ceiling on propagation directives beyond the initial plan, so a misbehaving
# contract chain can never run away even if the per-key cycle guard is bypassed.
MAX_PROPAGATION_DIRECTIVES = 12


@dataclass
class RunResult:
    directives: list[DomainDirective]
    reports: list[LeadReport] = field(default_factory=list)
    environment_id: str = ""
    baseline: str = ""


class Orchestrator:
    def __init__(self, architect: Architect, sandbox_backend: AgentBackend,
                 repo_url: str, repo_dir: str = "/workspace/repo", subdir: str = ""):
        self.architect = architect
        self.backend = sandbox_backend
        self.repo_url = repo_url
        self.repo_dir = repo_dir
        self.subdir = subdir
        self.package_dir = f"{repo_dir}/{subdir}" if subdir else repo_dir

    def bootstrap(self) -> tuple[str, str]:
        """Provision the shared sandbox and clone+install the repo (no full baseline run).

        FastAPI-scale suites are too heavy to run wholesale; Leads run only the
        tests relevant to their change, so the baseline is just an importable install.
        """
        prompt = (
            f"Run: git clone --depth 1 {self.repo_url} {self.repo_dir}\n"
            f"Then install the package and its test dependencies (try `pip install -e .` "
            f"and any requirements-tests file), and verify it imports. Reply with ONLY JSON: "
            f'{{"installed": true, "summary": "..."}}'
        )
        turn = self.backend.start(BOOT_SYSTEM, prompt, environment="remote")
        return turn.environment_id, turn.text

    def run(self, topology: Topology, domains: list[Domain], intent: str,
            on_event=None) -> RunResult:
        emit = on_event or (lambda *a, **k: None)

        directives = self.architect.plan(topology, intent, domains)
        emit("planned", directives)

        env_id, baseline = self.bootstrap()
        emit("bootstrapped", baseline)

        domain_by_name = {d.name: d for d in domains}
        queue: deque[DomainDirective] = deque(directives)
        seen: set = set()
        reports: list[LeadReport] = []
        budget = len(directives) + MAX_PROPAGATION_DIRECTIVES

        while queue and budget > 0:
            budget -= 1
            directive = queue.popleft()
            domain = domain_by_name.get(directive.domain)
            if domain is None:
                continue
            emit("lead_start", directive)
            lead = DomainLead(self.backend, domain, self.repo_dir, self.package_dir)
            report, _ = lead.execute(directive, env_id)
            reports.append(report)
            emit("lead_done", report)
            props = self.architect.resolve_propagations(topology, domains, report, seen)
            if props:
                emit("propagating", props)
            queue.extend(props)

        return RunResult(directives=directives, reports=reports,
                         environment_id=env_id, baseline=baseline)
