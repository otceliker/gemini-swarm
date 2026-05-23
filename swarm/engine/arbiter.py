"""The Arbiter: a high-capability round-closer that converges the deliberation.

Each round it folds the workers' messages into append-only frozen decisions
(anti-oscillation), maintains the canon/bible, and decides whether the discussion
has converged. At the end it emits the ExecutionPlan the mutate phase consumes.

Intended to run on a stronger model than the workers (Pro-class vs Flash).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ._json import safe_extract_json
from .state import ExecutionPlan, Message, Segment, SharedMedium


class Completer(Protocol):
    def complete(self, system_instruction: str, prompt: str) -> str: ...


CLOSE_SYSTEM = (
    "You are the Arbiter, the senior editor converging a multi-agent discussion toward a "
    "consistent plan. Read this round's messages and freeze concrete DECISIONS that are now "
    "agreed. Decisions are APPEND-ONLY: only add new ones, never contradict earlier decisions. "
    "Maintain a short canon/bible of facts and voice. List remaining open questions. Also write "
    "a brief NOTE to the room — one or two sentences of guidance steering everyone toward "
    "convergence (a heads-up or nudge, not a question; workers keep it in mind, they do not reply "
    "to you). Decide whether the discussion has converged. Respond with JSON only."
)

CLOSE_PROMPT = """\
Overall goal: {goal}

Decisions already frozen:
{decisions}

Canon/bible so far:
{bible}

This round's messages:
{messages}

Open coordination pairings:
{pairings}

Respond ONLY:
{{"decisions": ["a newly agreed decision", "..."], "bible": "updated canon text",
  "open_questions": ["..."], "note": "one or two sentences of guidance to the room",
  "converged": false}}
"""

PLAN_SYSTEM = (
    "You are the Arbiter producing the final EXECUTION PLAN. Using the frozen decisions and the "
    "canon/bible, write ONE concrete, self-contained directive per segment for the execution "
    "phase, plus any global invariants every segment must respect. Respond with JSON only."
)

PLAN_PROMPT = """\
Overall goal: {goal}

Segments:
{segments}

Frozen decisions:
{decisions}

Canon/bible:
{bible}

Respond ONLY:
{{"directives": {{"<segment id>": "concrete instruction", "...": "..."}},
  "invariants": ["a global constraint", "..."]}}
"""


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) or "(none)"


def _render_messages(messages: list[Message]) -> str:
    return "\n".join(f"[{m.author}] {m.text}" for m in messages) or "(none)"


@dataclass
class Verdict:
    new_decisions: list[str] = field(default_factory=list)
    bible: str = ""
    open_questions: list[str] = field(default_factory=list)
    note: str = ""
    converged: bool = False


class Arbiter:
    def __init__(self, reasoner: Completer):
        self.reasoner = reasoner

    def close_round(self, goal: str, medium: SharedMedium,
                    round_messages: list[Message]) -> Verdict:
        prompt = CLOSE_PROMPT.format(
            goal=goal,
            decisions=_bullets(medium.decisions),
            bible=medium.bible or "(empty)",
            messages=_render_messages(round_messages),
            pairings=_bullets([f"{p.a} <-> {p.b}: {p.topic}" for p in medium.pairings]),
        )
        data = safe_extract_json(self.reasoner.complete(CLOSE_SYSTEM, prompt))
        raw_decisions = data.get("decisions") or data.get("new_decisions") or []
        return Verdict(
            new_decisions=[d for d in raw_decisions if isinstance(d, str)],
            bible=data.get("bible", "") or "",
            open_questions=[q for q in data.get("open_questions", []) if isinstance(q, str)],
            note=data.get("note", "") or "",
            converged=bool(data.get("converged", False)),
        )

    def make_plan(self, goal: str, segments: list[Segment], medium: SharedMedium) -> ExecutionPlan:
        prompt = PLAN_PROMPT.format(
            goal=goal,
            segments="\n".join(f"- {s.id}: {s.summary}" for s in segments) or "(none)",
            decisions=_bullets(medium.decisions),
            bible=medium.bible or "(empty)",
        )
        data = safe_extract_json(self.reasoner.complete(PLAN_SYSTEM, prompt))
        raw = data.get("directives")
        raw = raw if isinstance(raw, dict) else {}
        known = {s.id for s in segments}
        directives = {k: v for k, v in raw.items()
                      if k in known and isinstance(v, str) and v.strip()}
        for s in segments:                       # every segment gets a directive
            directives.setdefault(s.id, goal)    # fallback: the overall goal
        invariants = [x for x in data.get("invariants", []) if isinstance(x, str)]
        return ExecutionPlan(directives=directives, invariants=invariants, bible=medium.bible)
