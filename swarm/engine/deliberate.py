"""The deliberate phase: an R-round synchronous (Jacobi) deliberation.

Each round, every segment-worker reads the SAME frozen snapshot of prior rounds
and emits one message (+ coordination pairings + a stable flag). The Arbiter then
closes the round (freezing append-only decisions) and decides convergence. The
loop ends on convergence, all-workers-stable, or the round cap — then the Arbiter
emits the ExecutionPlan.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import events as E
from ._json import safe_extract_json
from .arbiter import Arbiter, Completer
from .events import EventBus
from .state import ExecutionPlan, Message, Pairing, Segment, SharedMedium

WORKER_SYSTEM = (
    "You are the worker agent for segment '{id}' in a coordinated multi-agent {kind} edit. "
    "You do NOT rewrite the content here — that happens in a later phase. Your only job is to "
    "COORDINATE: surface terminology, names, and facts that must stay consistent across "
    "segments (so the Arbiter can freeze them into the shared canon), and flag any boundary "
    "handoff you need with a neighbour. Keep your message SHORT — a few sentences of "
    "coordination notes, never a draft of the rewritten text. Propose a pairing when your "
    "segment depends on a fact another segment owns. Set stable=true once the canon your "
    "segment needs is settled. Respond with JSON only."
)

WORKER_PROMPT = """\
Overall goal: {goal}

Your segment: {sid}
Summary: {summary}
Coupled to: {relations}

Frozen decisions so far:
{decisions}

Canon/bible so far:
{bible}

Open questions:
{open_questions}

Last round's messages:
{snapshot}

Contribute brief COORDINATION NOTES (terminology/facts to align + boundary handoffs),
NOT a rewrite. Respond ONLY:
{{"message": "<short coordination notes>", "pairings": [{{"with": "<segment id>", "topic": "..."}}], "stable": false}}
"""


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) or "(none)"


def _render_messages(messages: list[Message]) -> str:
    return "\n".join(f"[{m.author}] {m.text}" for m in messages) or "(none)"


@dataclass
class Deliberation:
    worker_reasoner: Completer
    arbiter: Arbiter
    rounds: int = 10
    bus: EventBus | None = None

    def _emit(self, kind: str, **payload) -> None:
        if self.bus is not None:
            self.bus.emit(kind, **payload)

    def run(self, goal: str, segments: list[Segment]) -> tuple[SharedMedium, ExecutionPlan]:
        medium = SharedMedium()
        self._emit(E.PHASE, phase="deliberate")

        for r in range(1, self.rounds + 1):
            self._emit(E.ROUND, round=r, of=self.rounds)
            snapshot = medium.round_snapshot(r - 1)   # Jacobi: same prior snapshot for all
            round_messages: list[Message] = []

            for seg in segments:
                msg = self._worker_turn(goal, seg, medium, snapshot, r)
                medium.messages.append(msg)
                round_messages.append(msg)
                self._emit(E.MESSAGE, author=msg.author, round=r, text=msg.text, stable=msg.stable)
                for p in msg.pairings:
                    medium.pairings.append(p)
                    self._emit(E.PAIRING, a=p.a, b=p.b, topic=p.topic)

            verdict = self.arbiter.close_round(goal, medium, round_messages)
            for decision in verdict.new_decisions:
                if medium.freeze(decision):
                    self._emit(E.DECISION, text=decision)
            if verdict.bible:
                medium.bible = verdict.bible
            medium.open_questions = verdict.open_questions

            if verdict.converged or (round_messages and all(m.stable for m in round_messages)):
                break

        plan = self.arbiter.make_plan(goal, segments, medium)
        self._emit(E.PLAN, directives=plan.directives, invariants=plan.invariants)
        return medium, plan

    def _worker_turn(self, goal: str, seg: Segment, medium: SharedMedium,
                     snapshot: list[Message], r: int) -> Message:
        prompt = WORKER_PROMPT.format(
            goal=goal, sid=seg.id, summary=seg.summary or "(none)",
            relations=", ".join(seg.relations) or "(none)",
            decisions=_bullets(medium.decisions), bible=medium.bible or "(empty)",
            open_questions=_bullets(medium.open_questions), snapshot=_render_messages(snapshot),
        )
        text = self.worker_reasoner.complete(
            WORKER_SYSTEM.format(id=seg.id, kind=seg.kind), prompt)
        data = safe_extract_json(text)
        pairings = [
            Pairing(a=seg.id, b=p["with"], topic=p.get("topic", ""))
            for p in data.get("pairings", []) if isinstance(p, dict) and p.get("with")
        ]
        return Message(
            author=seg.id, round=r, text=str(data.get("message", "")).strip(),
            pairings=pairings, stable=bool(data.get("stable", False)),
        )
