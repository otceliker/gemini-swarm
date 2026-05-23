"""Prose modality: chunking (pure) + mutate/validate/stitch with a fake reasoner."""
from __future__ import annotations

from swarm.engine.state import ExecutionPlan
from swarm.modalities.prose import ProseModality, chunk_text


class ProseFake:
    """Returns prose for rewrite, JSON for validate (dispatched on the system prompt)."""

    def complete(self, system: str, prompt: str) -> str:
        if "json" in system.lower():
            return '{"ok": true, "issues": []}'
        return "REWRITTEN: a chapter where the doom is discussed"


def test_chunk_text_splits_by_paragraph_and_respects_max():
    text = "para one.\n\n" + ("x" * 50) + "\n\n" + ("y" * 50)
    chunks = chunk_text(text, max_chars=60)
    assert len(chunks) >= 2
    assert all(len(c) <= 120 for c in chunks)  # no single paragraph blew way past the cap


def test_ingest_chunks_and_links_adjacency():
    m = ProseModality(reasoner=ProseFake(), max_chars=10)
    segs = m.ingest("aaaa\n\nbbbb\n\ncccc")
    assert len(segs) >= 2
    assert segs[1].id in segs[0].relations          # forward link
    assert segs[0].id in segs[1].relations          # back link


def test_mutate_validate_and_stitch():
    m = ProseModality(reasoner=ProseFake())
    segs = m.ingest("the original paragraph text goes here")
    plan = ExecutionPlan(directives={segs[0].id: "make it about AI doom"},
                         invariants=["keep voice"], bible="Raskolnikov fears AI")
    res = m.mutate(segs[0], plan)
    assert res.ok
    assert segs[0].meta["rewritten"].startswith("REWRITTEN")
    rep = m.validate(segs[0], plan)
    assert rep.ok
    assert "REWRITTEN" in m.stitch(segs)
