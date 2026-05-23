"""Offline tests for the deliberate phase (Jacobi loop + Arbiter), via a role-dispatching fake."""
from __future__ import annotations

from swarm.engine.arbiter import Arbiter
from swarm.engine.deliberate import Deliberation
from swarm.engine.events import ARBITER, DECISION, PLAN, ROUND, EventBus
from swarm.engine.state import Segment

SEGS = [Segment(id="s1", kind="prose", summary="chapter 1"),
        Segment(id="s2", kind="prose", summary="chapter 2")]

WORKER = '{"message":"I will align chapter 1","pairings":[{"with":"s2","topic":"fact F"}],"stable":false}'
WORKER_STABLE = '{"message":"nothing further","stable":true}'
CLOSE_CONVERGED = '{"decisions":["F is canon"],"bible":"canon v1","open_questions":[],"converged":true}'
CLOSE_OPEN = '{"decisions":[],"converged":false}'
PLAN_FULL = '{"directives":{"s1":"rewrite ch1 with F","s2":"rewrite ch2 with F"},"invariants":["keep voice"]}'


class RoleFake:
    """One completer that answers by role, detected from the system prompt."""

    def __init__(self, worker: str, closes: list[str], plan: str):
        self.worker = worker
        self.closes = closes
        self.plan = plan
        self.close_i = 0
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.prompts.append((system, prompt))
        s = system.lower()
        if "worker agent" in s:               # check worker first (its prompt mentions "Arbiter")
            return self.worker
        if "execution plan" in s:
            return self.plan
        out = self.closes[min(self.close_i, len(self.closes) - 1)]
        self.close_i += 1
        return out


def _delib(worker, closes, plan, rounds=10, bus=None):
    fake = RoleFake(worker, closes, plan)
    return Deliberation(worker_reasoner=fake, arbiter=Arbiter(fake), rounds=rounds, bus=bus)


def test_converges_and_produces_plan():
    bus = EventBus()
    medium, plan = _delib(WORKER, [CLOSE_CONVERGED], PLAN_FULL, bus=bus).run("inject F", SEGS)
    assert len(medium.messages) == 2            # one round, two workers
    assert medium.decisions == ["F is canon"]
    assert medium.bible == "canon v1"
    assert plan.directives == {"s1": "rewrite ch1 with F", "s2": "rewrite ch2 with F"}
    assert any(p.b == "s2" for p in medium.pairings)
    kinds = {e.kind for e in bus.history}
    assert {ROUND, DECISION, PLAN} <= kinds


def test_all_stable_terminates_after_one_round():
    medium, _ = _delib(WORKER_STABLE, [CLOSE_OPEN], PLAN_FULL, rounds=5).run("g", SEGS)
    assert len(medium.messages) == 2            # stopped early though arbiter said not converged


def test_runs_to_cap_when_never_converging():
    medium, _ = _delib(WORKER, [CLOSE_OPEN], PLAN_FULL, rounds=3).run("g", SEGS)
    assert len(medium.messages) == 6            # 2 workers x 3 rounds


def test_decisions_frozen_append_only_across_rounds():
    dup = '{"decisions":["F is canon"],"converged":false}'
    medium, _ = _delib(WORKER, [dup], PLAN_FULL, rounds=3).run("g", SEGS)
    assert medium.decisions == ["F is canon"]   # repeated across rounds, frozen once


def test_arbiter_note_streamed_and_seen_next_round():
    bus = EventBus()
    close_note = '{"decisions":[],"note":"Align on the terminology for the gods.","converged":false}'
    delib = _delib(WORKER, [close_note], PLAN_FULL, rounds=2, bus=bus)
    delib.run("g", SEGS)
    fake = delib.worker_reasoner
    # the Arbiter's note was emitted onto the stream
    assert any(e.kind == ARBITER and "terminology for the gods" in e.payload["text"]
               for e in bus.history)
    # and round-2 workers received it in their prompt
    worker_prompts = [pr for sys, pr in fake.prompts if "worker agent" in sys.lower()]
    assert any("Align on the terminology for the gods" in pr for pr in worker_prompts)


def test_plan_fallback_fills_missing_directives():
    plan_missing = '{"directives":{"s1":"do s1"},"invariants":[]}'
    _, plan = _delib(WORKER, [CLOSE_CONVERGED], plan_missing).run("the-goal", SEGS)
    assert plan.directives["s1"] == "do s1"
    assert plan.directives["s2"] == "the-goal"  # fallback to the overall goal
