"""Typed event stream — the seam between the engine and any UI (TUI, web, …).

The engine never touches a UI directly; it emits Events onto an EventBus, and UIs
subscribe. A broken UI sink can never crash the engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

# --- event kinds -----------------------------------------------------------
PHASE = "phase"            # {"phase": "ingest"|"deliberate"|"mutate"|"done"}
SEGMENTS = "segments"      # {"segments": [id, ...]}
ROUND = "round"            # {"round": int, "of": int}
MESSAGE = "message"        # {"author": id, "round": int, "text": str, "stable": bool}
PAIRING = "pairing"        # {"a": id, "b": id, "topic": str}
DECISION = "decision"      # {"text": str}   (a newly frozen decision)
ARBITER = "arbiter"        # {"round": int, "text": str}  (Arbiter's steering note to the room)
PLAN = "plan"              # {"directives": {...}, "invariants": [...]}
MUTATION = "mutation"      # {"segment": id, "state": "start"|"done", "ok": bool, "summary": str}
VALIDATION = "validation"  # {"segment": id, "ok": bool, "issues": [...]}
LOG = "log"                # {"text": str}


@dataclass
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


EventSink = Callable[[Event], None]


@dataclass
class EventBus:
    sinks: list[EventSink] = field(default_factory=list)
    history: list[Event] = field(default_factory=list)

    def subscribe(self, sink: EventSink) -> None:
        self.sinks.append(sink)

    def emit(self, kind: str, **payload: Any) -> Event:
        event = Event(kind=kind, payload=payload)
        self.history.append(event)
        for sink in self.sinks:
            try:
                sink(event)
            except Exception:
                # A misbehaving UI sink must never take down the engine.
                pass
        return event
