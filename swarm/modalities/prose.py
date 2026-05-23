"""Prose modality: edit a large text by chunking it and rewriting chunks in parallel.

Needs no sandbox — pure `generate_content` — so it's faster and cheaper than the
code modality. ingest=chunk, mutate=rewrite-against-bible, validate=consistency check.
"""
from __future__ import annotations

import os
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser

from ..engine._json import safe_extract_json
from ..engine.arbiter import Completer
from ..engine.modality import MutationResult, ValidationReport
from ..engine.state import ExecutionPlan, Segment

REWRITE_SYSTEM = (
    "You transform ONE passage of a larger work, applying the canon below. Be FAITHFUL to the "
    "source: keep the SAME events in the SAME order, the same structure, and roughly the same "
    "length. Restyle terminology, names, and framing per the canon — but do NOT add new "
    "material, remove content, reorder passages, or pull in anything from neighbouring passages. "
    "Output ONLY the transformed passage — no preamble, no quotes, no commentary."
)

REWRITE_PROMPT = """\
Transformation to apply: {directive}

Canon (terminology, names, and facts to use consistently):
{bible}

Invariants:
{invariants}
{corrections}
Passage to transform (preserve its content and ORDER exactly — restyle only):
\"\"\"
{text}
\"\"\"

Output only the transformed passage.
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


class _HTMLText(HTMLParser):
    """Strip an (X)HTML document to plain text, inserting paragraph breaks."""

    _BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "blockquote", "tr", "section"}

    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self._buf.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._buf.append("\n\n")

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def get_text(self) -> str:
        text = "".join(self._buf)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _doc_order_key(name: str) -> int:
    nums = re.findall(r"\d+", name.rsplit("/", 1)[-1])
    return int(nums[-1]) if nums else 0


def epub_to_text(path: str) -> str:
    """Extract plain text from an EPUB (zip of XHTML), in document order."""
    with zipfile.ZipFile(path) as z:
        docs = sorted(
            (n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))),
            key=_doc_order_key,
        )
        parts = []
        for name in docs:
            parser = _HTMLText()
            parser.feed(z.read(name).decode("utf-8", "replace"))
            text = parser.get_text()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _fetch(source: str) -> str:
    if source.lower().endswith(".epub") and os.path.exists(source):
        return epub_to_text(source)
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
    max_segments: int = 0          # 0 = all; >0 caps chunks (validate on a slice first)
    start_segment: int = 0         # skip leading chunks (e.g. front matter)
    name: str = "prose"

    def ingest(self, source: str) -> list[Segment]:
        chunks = chunk_text(_fetch(source), self.max_chars)
        if self.start_segment or self.max_segments:
            end = self.start_segment + self.max_segments if self.max_segments > 0 else None
            chunks = chunks[self.start_segment:end]
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

    def mutate(self, segment: Segment, plan: ExecutionPlan, feedback: str = "") -> MutationResult:
        corrections = (f"\nFix these issues flagged in a prior validation:\n{feedback}\n"
                       if feedback else "")
        prompt = REWRITE_PROMPT.format(
            directive=plan.directives.get(segment.id, "") or "(none)",
            bible=plan.bible or "(none)", invariants=_invariants(plan),
            corrections=corrections, text=segment.meta.get("text", ""),
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
