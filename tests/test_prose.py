"""Prose modality: chunking (pure) + mutate/validate/stitch with a fake reasoner."""
from __future__ import annotations

import zipfile

import pytest

from swarm.engine.state import ExecutionPlan
from swarm.modalities.prose import ProseModality, _fetch, chunk_text, epub_to_text


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


def test_max_segments_caps_chunks():
    m = ProseModality(reasoner=ProseFake(), max_chars=10, max_segments=2)
    segs = m.ingest("aaaa\n\nbbbb\n\ncccc\n\ndddd")
    assert len(segs) == 2


def test_epub_to_text_rejects_non_zip_with_clear_error(tmp_path):
    bad = tmp_path / "fake.epub"
    bad.write_text("definitely not a zip archive")
    with pytest.raises(ValueError, match="not a valid EPUB"):
        epub_to_text(str(bad))


def test_epub_to_text_reads_directory_package(tmp_path):
    pkg = tmp_path / "book.epub"          # an unzipped epub "package" (a directory)
    (pkg / "OEBPS").mkdir(parents=True)
    (pkg / "OEBPS" / "x-0.xhtml").write_text("<html><body><p>Chapter one.</p></body></html>")
    (pkg / "OEBPS" / "x-1.xhtml").write_text("<html><body><p>Chapter two.</p></body></html>")
    text = epub_to_text(str(pkg))
    assert "Chapter one." in text and "Chapter two." in text
    assert text.index("one") < text.index("two")


def test_fetch_handles_quoted_epub_path(tmp_path):
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("OEBPS/x-0.xhtml", "<html><body><p>Hello world.</p></body></html>")
    assert "Hello world." in _fetch(f'"{p}"')   # drag-and-drop quoting tolerated


def test_epub_to_text_extracts_in_order_and_strips_scripts(tmp_path):
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("OEBPS/x-0.xhtml",
                   "<html><body><h1>Book I</h1><p>Sing, O Muse.</p>"
                   "<script>tracker()</script></body></html>")
        z.writestr("OEBPS/x-1.xhtml",
                   "<html><body><p>Tell me, O Muse, of Ulysses.</p></body></html>")
    text = epub_to_text(str(path))
    assert "Sing, O Muse." in text
    assert "Ulysses" in text
    assert "tracker()" not in text                       # script content stripped
    assert text.index("Sing") < text.index("Ulysses")    # document order preserved


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
