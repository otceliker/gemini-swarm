"""Prose modality: edit a large text by chunking it and rewriting chunks in parallel.

Needs no sandbox — pure `generate_content` — so it's faster and cheaper than the
code modality. ingest=chunk, mutate=rewrite-against-bible, validate=consistency check.
"""
from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass

from ..engine._json import safe_extract_json
from ..engine.arbiter import Completer
from ..engine.modality import MutationResult, ValidationReport
from ..engine.state import ExecutionPlan, Segment

REWRITE_SYSTEM = (
    "You rewrite ONE chunk of a larger work to satisfy a directive while staying consistent "
    "with the shared canon. Preserve the original prose style and approximate length. "
    "Output ONLY the rewritten chunk text — no preamble, no quotes, no commentary."
)

REWRITE_PROMPT = """\
Directive for this chunk: {directive}

Canon/bible (stay consistent with this):
{bible}

Global invariants:
{invariants}

Original chunk:
\"\"\"
{text}
\"\"\"

Rewrite the chunk now. Output only the rewritten text.
"""

VALIDATE_SYSTEM = (
    "You check whether a rewritten chunk respects the shared canon and the global invariants. "
    "Respond with JSON only."
)

VALIDATE_PROMPT = """\
Canon/bible:
{bible}

Global invariants:
{invariants}

Rewritten chunk:
{text}

Respond ONLY: {{"ok": true, "issues": ["any inconsistency with the canon/invariants"]}}
"""


def _fetch(source: str) -> str:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source) as resp:  # noqa: S310 (user-supplied URL is intended)
            return resp.read().decode("utf-8", "replace")
    if os.path.exists(source):
        with open(source, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return source  # treat as raw text (handy for tests)


def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Group paragraphs into chunks no larger than ~max_chars."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        if cur and len(cur) + len(p) + 2 > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def _invariants(plan: ExecutionPlan) -> str:
    return "\n".join(f"- {x}" for x in plan.invariants) or "(none)"


@dataclass
class ProseModality:
    reasoner: Completer
    max_chars: int = 4000
    name: str = "prose"

    def ingest(self, source: str) -> list[Segment]:
        chunks = chunk_text(_fetch(source), self.max_chars)
        segments = [
            Segment(id=f"chunk-{i:04d}", kind="prose",
                    summary=" ".join(c.split())[:80], meta={"text": c})
            for i, c in enumerate(chunks)
        ]
        for i, seg in enumerate(segments):  # adjacency relations (continuity coupling)
            if i > 0:
                seg.relations.append(segments[i - 1].id)
            if i < len(segments) - 1:
                seg.relations.append(segments[i + 1].id)
        return segments

    def mutate(self, segment: Segment, plan: ExecutionPlan) -> MutationResult:
        prompt = REWRITE_PROMPT.format(
            directive=plan.directives.get(segment.id, "") or "(none)",
            bible=plan.bible or "(none)", invariants=_invariants(plan),
            text=segment.meta.get("text", ""),
        )
        try:
            new_text = self.reasoner.complete(REWRITE_SYSTEM, prompt).strip()
        except Exception as exc:
            return MutationResult(segment_id=segment.id, ok=False, summary=f"error: {exc}")
        if not new_text:
            return MutationResult(segment_id=segment.id, ok=False, summary="empty rewrite")
        segment.meta["rewritten"] = new_text
        return MutationResult(
            segment_id=segment.id, ok=True, output_ref=segment.id,
            summary=f"rewrote {len(segment.meta.get('text', ''))}->{len(new_text)} chars",
        )

    def validate(self, segment: Segment, plan: ExecutionPlan) -> ValidationReport:
        text = segment.meta.get("rewritten", segment.meta.get("text", ""))
        prompt = VALIDATE_PROMPT.format(
            bible=plan.bible or "(none)", invariants=_invariants(plan), text=text[:4000])
        data = safe_extract_json(self.reasoner.complete(VALIDATE_SYSTEM, prompt))
        return ValidationReport(
            segment_id=segment.id, ok=bool(data.get("ok", True)),
            issues=[x for x in data.get("issues", []) if isinstance(x, str)],
        )

    def stitch(self, segments: list[Segment]) -> str:
        """Reassemble the (rewritten) chunks into the final document."""
        return "\n\n".join(s.meta.get("rewritten", s.meta.get("text", "")) for s in segments)
