"""Modality-agnostic core models shared by all three phases."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    """One editable unit — a code domain/module, a prose chunk, etc."""

    id: str
    kind: str                                              # modality tag: "code" | "prose" | …
    summary: str = ""                                      # short description used in deliberation
    relations: list[str] = field(default_factory=list)     # ids this segment is coupled to
    meta: dict[str, Any] = field(default_factory=dict)     # modality-specific (path, span, modules…)


@dataclass
class Pairing:
    """A coordination link two segments raised during deliberation."""

    a: str
    b: str
    topic: str = ""


@dataclass
class Message:
    """One worker's contribution in a single deliberation round."""

    author: str                                            # segment id
    round: int
    text: str
    pairings: list[Pairing] = field(default_factory=list)
    stable: bool = False                                   # worker has nothing further to add


@dataclass
class SharedMedium:
    """The shared deliberation state. `decisions` are append-only (anti-oscillation)."""

    bible: str = ""                                        # evolving canon / invariants
    arbiter_note: str = ""                                 # latest Arbiter steering note (workers read it)
    messages: list[Message] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)     # frozen; never rewritten
    open_questions: list[str] = field(default_factory=list)
    pairings: list[Pairing] = field(default_factory=list)

    def round_snapshot(self, upto_round: int) -> list[Message]:
        """All messages through `upto_round` — the frozen view workers read next round."""
        return [m for m in self.messages if m.round <= upto_round]

    def freeze(self, decision: str) -> bool:
        """Append a decision once. Returns True if newly added (for event emission)."""
        decision = decision.strip()
        if decision and decision not in self.decisions:
            self.decisions.append(decision)
            return True
        return False


@dataclass
class ExecutionPlan:
    """Output of the deliberate phase; consumed by the mutate phase."""

    directives: dict[str, str] = field(default_factory=dict)   # segment id -> instruction
    invariants: list[str] = field(default_factory=list)        # global constraints / canon facts
    bible: str = ""
