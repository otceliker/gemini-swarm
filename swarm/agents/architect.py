"""The Architect agent: global planner, segmenter, and propagation coordinator.

Runs entirely on the reasoning path (raw model, no sandbox). It:
  - segments the topology into domains (Phase 1.4),
  - plans a user intent into per-domain directives,
  - and, when a Domain Lead reports a cross-boundary contract change, resolves
    *which other domains must adapt* using the dependency graph (pure Python).

Cross-agent coordination is centralized here by design: Leads never call each
other; they report to the Architect, which serializes and routes propagation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..protocol.models import Domain, DomainDirective, LeadReport, Topology
from .backend import Reasoner

SYSTEM_INSTRUCTION = (
    "You are the Architect agent in a multi-agent codebase swarm. You receive a "
    "deterministic dependency map of a Python repository and partition its modules "
    "into a small number (2-6) of cohesive, loosely-coupled domains, each suitable "
    "for one engineer agent to own. Group modules that depend on each other; keep "
    "cross-domain edges minimal. Respond with JSON only, no prose."
)

PROMPT_TEMPLATE = """\
Here is the dependency map. Each line is:
  <module> | deps: [internal modules it imports] | public: [public symbols]

{digest}

Partition these modules into 2-6 domains. Assign every module that has symbols or
dependencies to exactly one domain. Respond with ONLY this JSON shape:

{{"domains": [{{"name": "short_name", "module_names": ["a.b", ...], "rationale": "one sentence"}}]}}
"""

PLAN_SYSTEM = (
    "You are the Architect agent. Given a repository's domains and a high-level user "
    "intent, produce concrete per-domain work directives. Assign work ONLY to the "
    "domains that must change to satisfy the intent. Respond with JSON only, no prose."
)

PLAN_TEMPLATE = """\
Domains (name: modules):
{domains}

User intent:
{intent}

Produce directives as ONLY this JSON shape, including only domains that need changes:

{{"directives": [{{"domain": "<existing domain name>", "instruction": "what this domain must do", "target_modules": ["a.b", ...]}}]}}
"""


@dataclass
class SegmentationResult:
    domains: list[Domain]
    unassigned: list[str] = field(default_factory=list)
    raw: str = ""


def _extract_json(text: str):
    """Pull the first balanced JSON object/array out of an LLM response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    candidates = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not candidates:
        raise ValueError("no JSON found in agent response")
    start = min(candidates)
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"

    depth = 0
    in_str = esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in agent response")


def digest_topology(topology: Topology) -> str:
    lines = []
    for m in topology.modules:
        public = ", ".join(s.name for s in m.symbols if s.is_public)
        deps = ", ".join(m.internal_deps)
        lines.append(f"- {m.module_name} | deps: [{deps}] | public: [{public}]")
    return "\n".join(lines)


def _domains_digest(domains: list[Domain]) -> str:
    return "\n".join(f"- {d.name}: {', '.join(d.module_names)}" for d in domains)


def _reverse_deps(topology: Topology) -> dict[str, set[str]]:
    """module -> set of internal modules that import it."""
    rev: dict[str, set[str]] = {}
    for m in topology.modules:
        for dep in m.internal_deps:
            rev.setdefault(dep, set()).add(m.module_name)
    return rev


class Architect:
    def __init__(self, reasoner: Reasoner):
        self.reasoner = reasoner

    def segment(self, topology: Topology) -> SegmentationResult:
        text = self.reasoner.complete(
            SYSTEM_INSTRUCTION, PROMPT_TEMPLATE.format(digest=digest_topology(topology)))
        data = _extract_json(text)
        raw_domains = data["domains"] if isinstance(data, dict) else data

        known = set(topology.module_names())
        domains: list[Domain] = []
        assigned: set[str] = set()
        for d in raw_domains:
            valid = [m for m in d.get("module_names", []) if m in known]
            assigned.update(valid)
            domains.append(Domain(
                name=d.get("name", "unnamed"), module_names=valid,
                rationale=d.get("rationale", ""),
            ))

        assignable = {m.module_name for m in topology.modules if m.symbols or m.internal_deps}
        return SegmentationResult(
            domains=domains, unassigned=sorted(assignable - assigned), raw=text)

    def plan(self, topology: Topology, intent: str, domains: list[Domain]) -> list[DomainDirective]:
        """Decompose a high-level intent into validated per-domain directives."""
        text = self.reasoner.complete(
            PLAN_SYSTEM, PLAN_TEMPLATE.format(domains=_domains_digest(domains), intent=intent))
        data = _extract_json(text)
        raw = data["directives"] if isinstance(data, dict) else data

        names = {d.name for d in domains}
        known = set(topology.module_names())
        directives: list[DomainDirective] = []
        for r in raw:
            if r.get("domain") not in names:
                continue  # drop hallucinated domains
            directives.append(DomainDirective(
                domain=r["domain"],
                instruction=r.get("instruction", ""),
                target_modules=[m for m in r.get("target_modules", []) if m in known],
                kind="primary",
            ))
        return directives

    def resolve_propagations(self, topology: Topology, domains: list[Domain],
                             report: LeadReport, seen: set) -> list[DomainDirective]:
        """Given a Lead's contract changes, find which *other* domains must adapt.

        Pure graph logic — no LLM. `seen` is the cycle guard: a given
        (domain, module, symbol) propagation is issued at most once across a run.
        """
        rev = _reverse_deps(topology)
        domain_of = {m: d.name for d in domains for m in d.module_names}
        directives: list[DomainDirective] = []

        for cc in report.contract_changes:
            by_domain: dict[str, list[str]] = {}
            for importer in rev.get(cc.target_module, set()):
                dname = domain_of.get(importer)
                if dname and dname != report.domain:
                    by_domain.setdefault(dname, []).append(importer)

            for dname, mods in sorted(by_domain.items()):
                key = (dname, cc.target_module, cc.target_symbol)
                if key in seen:
                    continue
                seen.add(key)
                directives.append(DomainDirective(
                    domain=dname,
                    instruction=(
                        f"A dependency changed: `{cc.target_module}.{cc.target_symbol}` now has "
                        f"signature `{cc.proposed_signature}` ({cc.reason}). Update its callers in "
                        f"{', '.join(sorted(mods))} and keep tests green."),
                    target_modules=sorted(mods),
                    kind="propagation",
                    origin=f"{cc.target_module}.{cc.target_symbol}",
                ))
        return directives
