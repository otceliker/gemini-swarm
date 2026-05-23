"""Verbose end-to-end modification run on FastAPI.

Loads the existing FastAPI topology, segments it, then runs the serialized
modification loop for a contained, backward-compatible task — printing each
phase live (run with `python -u` so the output file streams).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swarm.agents.architect import Architect
from swarm.agents.backend import GeminiReasoner, ManagedAgentBackend
from swarm.orchestrator import Orchestrator
from swarm.protocol.models import Topology

REPO_URL = "https://github.com/fastapi/fastapi"
TOPO = Path(".workspaces/fastapi/topology.json")

INTENT = (
    "Add a new optional keyword parameter `deprecated_reason: Optional[str] = None` to the "
    "public `Query()` function in the `param_functions` module, thread it through to the "
    "underlying `Query` parameter class in the `params` module (store it as an attribute), and "
    "add a small focused test asserting that `Query(default=None, deprecated_reason='use X instead')` "
    "constructs and stores the value. Keep the parameter fully optional and backward compatible, "
    "and ensure the existing parameter-related tests still pass. Do not modify OpenAPI generation."
)


def ts() -> str:
    return time.strftime("%H:%M:%S")


def on_event(stage: str, payload) -> None:
    if stage == "planned":
        print(f"\n[{ts()}] ── PLAN ── {len(payload)} directive(s):")
        for d in payload:
            print(f"    • [{d.domain}] ({d.kind}) {d.instruction[:140]}")
            if d.target_modules:
                print(f"        targets: {', '.join(d.target_modules)}")
    elif stage == "bootstrapped":
        print(f"\n[{ts()}] ── SANDBOX READY ──\n    {payload[:400]}")
    elif stage == "lead_start":
        print(f"\n[{ts()}] ── LEAD START [{payload.domain}] ──")
        print(f"    directive: {payload.instruction[:200]}")
    elif stage == "lead_done":
        r = payload
        mark = "PASS" if r.tests_passed else "FAIL"
        print(f"[{ts()}] ── LEAD DONE [{r.domain}] tests={mark} success={r.success} ──")
        print(f"    summary: {r.summary[:300]}")
        for cc in r.contract_changes:
            print(f"    contract change: {cc.target_module}.{cc.target_symbol} -> {cc.proposed_signature}")
    elif stage == "propagating":
        print(f"\n[{ts()}] ── ARCHITECT PROPAGATING to {[d.domain for d in payload]} ──")


def main() -> int:
    print(f"[{ts()}] loading topology + segmenting (cheap reasoning path)…")
    topo = Topology.from_dict(json.loads(TOPO.read_text()))
    architect = Architect(GeminiReasoner())
    seg = architect.segment(topo)
    print(f"[{ts()}] {len(seg.domains)} domains: {[d.name for d in seg.domains]}")

    orch = Orchestrator(architect, ManagedAgentBackend(), repo_url=REPO_URL, subdir="fastapi")
    print(f"[{ts()}] starting modification loop (this provisions a sandbox + installs FastAPI)…")
    result = orch.run(topo, seg.domains, INTENT, on_event=on_event)

    print(f"\n[{ts()}] ===== DONE =====")
    print(f"workspace env: {result.environment_id}")
    for r in result.reports:
        print(f"  [{r.domain}] tests_passed={r.tests_passed} success={r.success}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
