"""The three-phase engine: ingest+segment -> deliberate -> mutate (+validate).

Modality-agnostic. The deliberate phase resolves cross-segment consistency up
front, which is what makes the mutate phase safe to run in parallel.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import events as E
from .deliberate import Deliberation
from .events import EventBus
from .modality import Modality, MutationResult, ValidationReport
from .state import ExecutionPlan, Segment, SharedMedium


@dataclass
class EngineResult:
    segments: list[Segment]
    medium: SharedMedium
    plan: ExecutionPlan
    mutations: dict[str, MutationResult] = field(default_factory=dict)
    validations: dict[str, ValidationReport] = field(default_factory=dict)


@dataclass
class Engine:
    modality: Modality
    deliberation: Deliberation
    bus: EventBus | None = None
    max_workers: int = 8
    max_repairs: int = 1           # on a failed validation, re-mutate with the issues fed back

    def _emit(self, kind: str, **payload) -> None:
        if self.bus is not None:
            self.bus.emit(kind, **payload)

    def run(self, source: str, goal: str) -> EngineResult:
        if self.bus is not None and self.deliberation.bus is None:
            self.deliberation.bus = self.bus  # share the bus so deliberation events surface

        # 1. ingest + segment
        self._emit(E.PHASE, phase="ingest")
        segments = self.modality.ingest(source)
        self._emit(E.SEGMENTS,
                   segments=[{"id": s.id, "name": s.summary or s.id} for s in segments])

        # 2. deliberate (emits its own ROUND/MESSAGE/DECISION/PLAN events)
        medium, plan = self.deliberation.run(goal, segments)

        # 3. mutate (parallel — segments are disjoint and the plan pre-resolved coupling) + validate
        self._emit(E.PHASE, phase="mutate")
        mutations: dict[str, MutationResult] = {}
        validations: dict[str, ValidationReport] = {}

        def work(seg: Segment):
            self._emit(E.MUTATION, segment=seg.id, state="start")
            result = self.modality.mutate(seg, plan)
            self._emit(E.MUTATION, segment=seg.id, state="done",
                       ok=result.ok, summary=result.summary)
            report = self.modality.validate(seg, plan)
            self._emit(E.VALIDATION, segment=seg.id, ok=report.ok, issues=report.issues)

            # Self-heal: re-mutate with the validation issues fed back, then re-validate.
            attempt = 0
            while not report.ok and attempt < self.max_repairs:
                attempt += 1
                self._emit(E.MUTATION, segment=seg.id, state="start", repair=attempt)
                result = self.modality.mutate(seg, plan, feedback="; ".join(report.issues))
                self._emit(E.MUTATION, segment=seg.id, state="done", ok=result.ok,
                           summary=f"repair {attempt}: {result.summary}", repair=attempt)
                report = self.modality.validate(seg, plan)
                self._emit(E.VALIDATION, segment=seg.id, ok=report.ok, issues=report.issues)

            return seg.id, result, report

        if self.max_workers > 1 and len(segments) > 1:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                for sid, result, report in pool.map(work, segments):
                    mutations[sid], validations[sid] = result, report
        else:
            for seg in segments:
                sid, result, report = work(seg)
                mutations[sid], validations[sid] = result, report

        self._emit(E.PHASE, phase="done")
        return EngineResult(segments=segments, medium=medium, plan=plan,
                            mutations=mutations, validations=validations)
