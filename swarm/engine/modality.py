"""The plugin seam: every modality implements ingest / mutate / validate.

The deliberate phase, Arbiter, and event bus are modality-agnostic; only these
three operations differ between code, prose, and (later) other content types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .state import ExecutionPlan, Segment


@dataclass
class MutationResult:
    segment_id: str
    ok: bool
    summary: str = ""
    output_ref: str = ""          # where the new content lives (path, chunk id, …)


@dataclass
class ValidationReport:
    segment_id: str
    ok: bool
    issues: list[str] = field(default_factory=list)


class Modality(Protocol):
    """A content type the engine can edit. `name` tags its segments' `kind`."""

    name: str

    def ingest(self, source: str) -> list[Segment]:
        """Turn a source (repo URL, text URL/path) into coupled segments."""
        ...

    def mutate(self, segment: Segment, plan: ExecutionPlan) -> MutationResult:
        """Apply this segment's directive (parallel-safe: segments are disjoint)."""
        ...

    def validate(self, segment: Segment, plan: ExecutionPlan) -> ValidationReport:
        """Check the mutated segment against the plan's bible/invariants (the final pass)."""
        ...
