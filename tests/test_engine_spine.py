"""Tests for the engine spine: event bus + core state models."""
from __future__ import annotations

from swarm.engine.events import LOG, MESSAGE, EventBus
from swarm.engine.state import ExecutionPlan, Message, SharedMedium


def test_eventbus_emits_to_sinks_and_records_history():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.emit(MESSAGE, author="s1", round=1, text="hi")
    assert len(seen) == 1
    assert seen[0].kind == MESSAGE
    assert seen[0].payload["author"] == "s1"
    assert len(bus.history) == 1


def test_eventbus_survives_a_broken_sink():
    bus = EventBus()
    delivered = []
    bus.subscribe(lambda ev: (_ for _ in ()).throw(RuntimeError("ui crashed")))
    bus.subscribe(delivered.append)
    bus.emit(LOG, text="x")  # must not raise despite the broken sink
    assert len(delivered) == 1


def test_shared_medium_decisions_are_append_only():
    m = SharedMedium()
    assert m.freeze("Raskolnikov fears recursive self-improvement") is True
    assert m.freeze("Raskolnikov fears recursive self-improvement") is False  # dup ignored
    assert m.freeze("  ") is False  # empty ignored
    assert m.freeze("He cites alignment in every chapter") is True
    assert m.decisions == [
        "Raskolnikov fears recursive self-improvement",
        "He cites alignment in every chapter",
    ]


def test_round_snapshot_filters_by_round():
    m = SharedMedium()
    m.messages = [Message("s1", 1, "a"), Message("s2", 1, "b"), Message("s1", 2, "c")]
    assert len(m.round_snapshot(1)) == 2
    assert len(m.round_snapshot(2)) == 3


def test_execution_plan_holds_directives():
    plan = ExecutionPlan(directives={"s1": "do x"}, invariants=["keep voice"], bible="canon")
    assert plan.directives["s1"] == "do x"
    assert plan.invariants == ["keep voice"]
