"""Run the three-phase engine on a prose source (epub / txt / url / raw text).

  python -m swarm.prose_run <source> "<goal>" [--max-chars N] [--max-segments N] [--rounds R]

Streams the deliberation + mutation live to the terminal (a stand-in for the
event-driven UI), then stitches the rewritten text to --out.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .agents.backend import GeminiReasoner
from .engine import events as E
from .engine.arbiter import Arbiter
from .engine.deliberate import Deliberation
from .engine.engine import Engine
from .engine.events import Event, EventBus
from .modalities.prose import ProseModality


def _printer(ev: Event) -> None:
    k, p = ev.kind, ev.payload
    if k == E.PHASE:
        print(f"\n=== PHASE: {p['phase'].upper()} ===")
    elif k == E.SEGMENTS:
        names = [s.get("name", s["id"]) if isinstance(s, dict) else s for s in p["segments"]]
        print(f"{len(names)} segments: {names}")
    elif k == E.ROUND:
        print(f"\n-- round {p['round']}/{p['of']} --")
    elif k == E.MESSAGE:
        flag = " (stable)" if p.get("stable") else ""
        print(f"  [{p['author']}]{flag}: {p['text']}")
    elif k == E.PAIRING:
        print(f"  pairing {p['a']} <-> {p['b']}: {p['topic']}")
    elif k == E.DECISION:
        print(f"  FROZEN: {p['text']}")
    elif k == E.ARBITER:
        print(f"  🏛 Arbiter ▸ {p['text']}")
    elif k == E.PLAN:
        print(f"  PLAN: {len(p['directives'])} directives; invariants={p['invariants']}")
    elif k == E.MUTATION and p.get("state") == "done":
        print(f"  mutate {p['segment']}: ok={p.get('ok')} {p.get('summary')}")
    elif k == E.VALIDATION:
        print(f"  validate {p['segment']}: ok={p['ok']} {p.get('issues') or ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swarm-prose")
    ap.add_argument("source", help="epub / txt file / URL / raw text")
    ap.add_argument("goal", help="the transformation to apply")
    ap.add_argument("--max-chars", type=int, default=4000)
    ap.add_argument("--max-segments", type=int, default=0, help="cap chunks (0 = whole text)")
    ap.add_argument("--start", type=int, default=0, help="skip this many leading chunks")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--out", default="rewritten.txt")
    args = ap.parse_args(argv)

    reasoner = GeminiReasoner()
    bus = EventBus()
    bus.subscribe(_printer)
    modality = ProseModality(reasoner=reasoner, max_chars=args.max_chars,
                             max_segments=args.max_segments, start_segment=args.start)
    deliberation = Deliberation(worker_reasoner=reasoner, arbiter=Arbiter(reasoner),
                                rounds=args.rounds, bus=bus)
    engine = Engine(modality=modality, deliberation=deliberation, bus=bus)

    t0 = time.time()
    result = engine.run(args.source, args.goal)
    final = modality.stitch(result.segments)
    Path(args.out).write_text(final, encoding="utf-8")
    print(f"\nwrote {len(final)} chars to {args.out} in {time.time() - t0:.1f}s")

    print("\n=== CANON / BIBLE ===\n" + (result.plan.bible or "(none)"))
    if result.segments:
        s0 = result.segments[0]
        print("\n=== SAMPLE BEFORE ===\n" + s0.meta.get("text", "")[:700])
        print("\n=== SAMPLE AFTER ===\n" + s0.meta.get("rewritten", "")[:700])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
