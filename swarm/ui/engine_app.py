"""Watchable TUI for the universal-editor engine — a thin consumer of the event stream.

Source + goal in; then the deliberation streams as a live channel (rounds, agent
messages, coordination pairings), frozen decisions accrue in the Bible panel, and
segment glyphs flip 🔵→🟡→🟢/🔴 through mutate+validate (validation-driven, with
self-heal). Click a chunk in the roster to see its before/after; the 'Final text'
tab holds the whole stitched output once done.

  python -m swarm.ui.engine_app
  ./.venv/bin/python -m swarm.serve            # browser
"""
from __future__ import annotations

import os
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import (Footer, Header, Input, OptionList, RichLog, Static,
                             TabbedContent, TabPane)
from textual.widgets.option_list import Option

from ..agents.backend import GeminiReasoner
from ..engine import events as E
from ..engine.arbiter import Arbiter
from ..engine.deliberate import Deliberation
from ..engine.engine import Engine
from ..engine.events import Event, EventBus
from ..modalities.prose import ProseModality

DEFAULT_SOURCE = os.path.expanduser("~/Downloads/pg1727-images-3.epub")
MAX_CHARS = 4000
MAX_SEGMENTS = 4
START_SEGMENT = 5
ROUNDS = 4
OUT = ".workspaces/engine_out.txt"

GLYPH = {"idle": "⚪", "analyzing": "🔵", "mutating": "🟡", "passed": "🟢", "failed": "🔴"}


class EngineApp(App):
    TITLE = "gemini-swarm · universal editor"
    CSS = """
    #tabs { height: 1fr; }          /* fill remaining space so the input stays visible */
    #prompt { height: 3; }
    #run_row { height: 1fr; }
    #roster { width: 38; border-right: solid $accent; }
    #channel { width: 2fr; padding: 0 1; }
    #bible { width: 1fr; border-left: solid $accent; padding: 0 1; }
    #chunkview, #finalview { padding: 1 2; height: 1fr; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.phase = "source"            # source -> goal -> running -> done
        self.source = ""
        self.segments: list[str] = []
        self.state: dict[str, str] = {}
        self.names: dict[str, str] = {}      # id -> self-given title
        self.outputs: dict[str, tuple[str, str]] = {}   # id -> (before, after)
        self._failed: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="run", id="tabs"):
            with TabPane("Run", id="run"):
                with Horizontal(id="run_row"):
                    yield OptionList(id="roster")
                    yield RichLog(id="channel", markup=True, wrap=True)
                    yield RichLog(id="bible", markup=True, wrap=True)
            with TabPane("Chunk", id="chunk"):
                yield RichLog(id="chunkview", markup=True, wrap=True)
            with TabPane("Final text", id="final"):
                yield RichLog(id="finalview", wrap=True)
        yield Input(value=DEFAULT_SOURCE, id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.channel = self.query_one("#channel", RichLog)
        self.bible = self.query_one("#bible", RichLog)
        self.roster = self.query_one("#roster", OptionList)
        self.chunkview = self.query_one("#chunkview", RichLog)
        self.finalview = self.query_one("#finalview", RichLog)
        self.bible.write("[b]CANON / DECISIONS[/]")
        self.chunkview.write("[dim]Select a chunk in the roster (Run tab) to see its before/after here.[/]")
        self.channel.write("[b]🐝 universal editor[/] — confirm or edit the source, then press Enter.")
        self.query_one("#prompt", Input).focus()

    # ---- helpers ----
    def _nm(self, sid: str) -> str:
        return self.names.get(sid, sid)

    def _render_roster(self) -> None:
        self.roster.clear_options()
        opts = [
            Option(f"{GLYPH.get(self.state.get(sid, 'idle'), '⚪')} {self._nm(sid)} (#{i + 1:04d})", id=sid)
            for i, sid in enumerate(self.segments)
        ]
        if opts:
            self.roster.add_options(opts)

    def _show_chunk(self, sid: str) -> None:
        self.chunkview.clear()
        self.chunkview.write(f"[b]{self._nm(sid)}[/]  [dim]{sid}[/]\n")
        before, after = self.outputs.get(sid, ("", ""))
        if not before and not after:
            self.chunkview.write("[dim](this chunk's output isn't ready yet — "
                                 "it appears once the run finishes)[/]")
            return
        if before:
            self.chunkview.write("[b]ORIGINAL[/]")
            self.chunkview.write(before)
        if after:
            self.chunkview.write("\n[b green]REWRITTEN[/]")
            self.chunkview.write(after)

    # ---- clickable roster ----
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        sid = getattr(event, "option_id", None) or getattr(event.option, "id", None)
        if sid:
            self.select_chunk(sid)

    def select_chunk(self, sid: str) -> None:
        self._show_chunk(sid)
        self.query_one("#tabs", TabbedContent).active = "chunk"

    # ---- event consumer (UI thread) ----
    def _on_event(self, ev: Event) -> None:
        k, p = ev.kind, ev.payload
        if k == E.PHASE:
            self.channel.write(f"\n[b on grey30] PHASE: {p['phase'].upper()} [/]")
        elif k == E.SEGMENTS:
            segs = p["segments"]
            self.segments = [s["id"] if isinstance(s, dict) else s for s in segs]
            self.names = {(s["id"] if isinstance(s, dict) else s):
                          (s.get("name") if isinstance(s, dict) else s) for s in segs}
            self.state = {sid: "idle" for sid in self.segments}
            self._render_roster()
            self.channel.write(f"[dim]{len(self.segments)} segments (click one to inspect)[/]")
        elif k == E.ROUND:
            self.channel.write(f"\n[b]── round {p['round']}/{p['of']} ──[/]")
        elif k == E.MESSAGE:
            self.state[p["author"]] = "analyzing"
            self._render_roster()
            flag = " [green](stable)[/]" if p.get("stable") else ""
            self.channel.write(f"[cyan]{self._nm(p['author'])}[/]{flag} ▸ {p['text']}")
        elif k == E.PAIRING:
            self.channel.write(f"   [magenta]⇄ {self._nm(p['a'])} → {self._nm(p['b'])}[/] [dim]{p['topic']}[/]")
        elif k == E.DECISION:
            self.bible.write(f"❄ {p['text']}")
        elif k == E.ARBITER:
            self.channel.write(f"[b yellow]🏛 Arbiter[/] ▸ [yellow]{p['text']}[/]")
        elif k == E.PLAN:
            self.channel.write(f"[b green]✓ plan ready[/] [dim]{len(p['directives'])} directives[/]")
        elif k == E.MUTATION:
            seg = p["segment"]
            if p.get("state") == "start":
                self.state[seg] = "mutating"
                if p.get("repair"):
                    self.channel.write(f"[yellow]↻ repairing {self._nm(seg)} (attempt {p['repair']})[/]")
            else:
                self.channel.write(f"[yellow]✎ {self._nm(seg)}[/] [dim]{p.get('summary', '')}[/]")
            self._render_roster()
        elif k == E.VALIDATION:
            seg = p["segment"]
            if p.get("ok"):
                self.state[seg] = "passed"
                if seg in self._failed:
                    self._failed.discard(seg)
                    self.channel.write(f"[green]✓ {self._nm(seg)} fixed[/]")
            else:
                self.state[seg] = "failed"
                self._failed.add(seg)
                self.channel.write(f"[red]✗ {self._nm(seg)}: {', '.join(p.get('issues') or [])}[/]")
            self._render_roster()

    # ---- input ----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        if self.phase == "source":
            self.source = value
            self.phase = "goal"
            event.input.value = ""
            event.input.placeholder = "describe the transformation (the goal)…"
            self.channel.write(f"[green]source:[/] {value}")
        elif self.phase == "goal":
            self.phase = "running"
            event.input.value = ""
            event.input.disabled = True
            event.input.placeholder = "running…"
            self.channel.write(f"[green]goal:[/] {value}\n[dim]ingesting + naming chunks…[/]")
            self.run_engine(self.source, value)

    # ---- engine worker ----
    @work(thread=True)
    def run_engine(self, source: str, goal: str) -> None:
        try:
            reasoner = GeminiReasoner()
            bus = EventBus()
            bus.subscribe(lambda ev: self.call_from_thread(self._on_event, ev))
            modality = ProseModality(reasoner=reasoner, max_chars=MAX_CHARS,
                                     max_segments=MAX_SEGMENTS, start_segment=START_SEGMENT)
            deliberation = Deliberation(worker_reasoner=reasoner, arbiter=Arbiter(reasoner),
                                        rounds=ROUNDS, bus=bus)
            engine = Engine(modality=modality, deliberation=deliberation, bus=bus)
            result = engine.run(source, goal)
            final = modality.stitch(result.segments)
            Path(OUT).parent.mkdir(parents=True, exist_ok=True)
            Path(OUT).write_text(final, encoding="utf-8")
            outputs = {s.id: (s.meta.get("text", ""), s.meta.get("rewritten", ""))
                       for s in result.segments}
            self.call_from_thread(self._on_done, final, outputs)
        except Exception as exc:
            self.call_from_thread(self.channel.write, f"\n[red]error:[/] {exc}")

    def _on_done(self, final: str, outputs: dict[str, tuple[str, str]]) -> None:
        self.outputs = outputs
        self.finalview.clear()
        self.finalview.write(final)
        self.channel.write(
            f"\n[b green]DONE[/] — wrote {len(final)} chars to {OUT}. "
            "Click a chunk to see its before/after; open the [b]Final text[/] tab for the whole thing.")


def main() -> None:
    EngineApp().run()


if __name__ == "__main__":
    main()
