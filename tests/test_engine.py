"""Engine wiring test: real Deliberation + a stub modality, all three phases."""
from __future__ import annotations

from swarm.engine.arbiter import Arbiter
from swarm.engine.deliberate import Deliberation
from swarm.engine.engine import Engine
from swarm.engine.events import PHASE, EventBus
from swarm.engine.modality import MutationResult, ValidationReport
from swarm.engine.state import Segment


class DelibFake:
    """Role-dispatching completer for the deliberation (converges immediately)."""

    def complete(self, system: str, prompt: str) -> str:
        s = system.lower()
        if "worker agent" in s:               # check worker first (its prompt mentions "Arbiter")
            return '{"message":"ok","stable":true}'
        if "execution plan" in s:
            return '{"directives":{"a":"do a","b":"do b"},"invariants":["keep voice"]}'
        return '{"decisions":["d1"],"converged":true}'


class StubModality:
    name = "stub"

    def ingest(self, source):
        return [Segment(id="a", kind="stub", summary="A"),
                Segment(id="b", kind="stub", summary="B")]

    def mutate(self, segment, plan, feedback=""):
        return MutationResult(segment_id=segment.id, ok=True,
                              summary=f"{segment.id}: {plan.directives.get(segment.id)}")

    def validate(self, segment, plan):
        return ValidationReport(segment_id=segment.id, ok=True)


class FlakyModality:
    """Fails validation on the first pass, passes once a repair has re-mutated."""

    name = "flaky"

    def __init__(self):
        self.calls: dict[str, int] = {}

    def ingest(self, source):
        return [Segment(id="a", kind="flaky", summary="A")]

    def mutate(self, segment, plan, feedback=""):
        self.calls[segment.id] = self.calls.get(segment.id, 0) + 1
        return MutationResult(segment_id=segment.id, ok=True,
                              summary=f"attempt {self.calls[segment.id]} fb={bool(feedback)}")

    def validate(self, segment, plan):
        ok = self.calls.get(segment.id, 0) >= 2
        return ValidationReport(segment_id=segment.id, ok=ok,
                                issues=[] if ok else ["inconsistent term"])


def test_engine_runs_three_phases_and_threads_plan_into_mutate():
    bus = EventBus()
    fake = DelibFake()
    delib = Deliberation(worker_reasoner=fake, arbiter=Arbiter(fake), rounds=3)
    result = Engine(modality=StubModality(), deliberation=delib, bus=bus, max_workers=1).run(
        "source", "the goal")

    assert set(result.mutations) == {"a", "b"}
    assert all(r.ok for r in result.mutations.values())
    assert set(result.validations) == {"a", "b"}
    # the Arbiter's per-segment directive reached the mutate phase
    assert "do a" in result.mutations["a"].summary
    # phases fired in order, deliberation events surfaced on the shared bus
    phases = [e.payload.get("phase") for e in bus.history if e.kind == PHASE]
    assert phases == ["ingest", "deliberate", "mutate", "done"]


def test_engine_repairs_failed_validation():
    fake = DelibFake()
    delib = Deliberation(worker_reasoner=fake, arbiter=Arbiter(fake), rounds=2)
    mod = FlakyModality()
    result = Engine(modality=mod, deliberation=delib, max_workers=1, max_repairs=1).run("s", "g")
    assert mod.calls["a"] == 2          # initial mutate + one repair
    assert result.validations["a"].ok   # fixed after the repair fed the issues back
