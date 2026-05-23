"""Watchable TUI for the universal-editor engine — a thin consumer of the event stream.

Source + goal in; then the deliberation streams as a live channel (rounds, agent
messages, coordination pairings), frozen decisions accrue in the Bible panel, and
segment glyphs flip 🔵→🟡→🟢/🔴 through mutate+validate. The UI knows nothing about
modalities; it just renders Events.

  python -m swarm.ui.engine_app
  ./.venv/bin/textual serve "./.venv/bin/python -m swarm.ui.engine_app"   # browser
"""
from __future__ import annotations

import os
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

from ..agents.backend import GeminiReasoner
from ..engine import events as E
from ..engine.arbiter import Arbiter
from ..engine.deliberate import Deliberation
from ..engine.engine import Engine
from ..engine.events import Event, EventBus
from ..modalities.prose import ProseModality

# Demo defaults (tuned to the validated Odyssey slice; edit freely).
DEFAULT_SOURCE = os.path.expanduser("~/Downloads/pg1727-images-3.epub")
MAX_CHARS = 4000
MAX_SEGMENTS = 20
START_SEGMENT = 5
ROUNDS = 4
OUT = ".workspaces/engine_out.txt"

GLYPH = {"idle": "⚪", "analyzing": "🔵", "mutating": "🟡", "passed": "🟢", "failed": "🔴"}


class EngineApp(App):
    TITLE = "gemini-swarm · universal editor"
    CSS = """
    #body { height: 1fr; }
    #roster { width: 28; border-right: solid $accent; padding: 1; }
    #channel { width: 2fr; padding: 0 1; }
    #bible { width: 1fr; border-left: solid $accent; padding: 0 1; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.phase = "source"          # source -> goal -> running -> done
        self.source = ""
        self.segments: list[str] = []
        self.state: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Static("🏛  Arbiter", id="roster")
            yield RichLog(id="channel", markup=True, wrap=True)
            yield RichLog(id="bible", markup=True, wrap=True)
        yield Input(value=DEFAULT_SOURCE, id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.channel = self.query_one("#channel", RichLog)
        self.roster = self.query_one("#roster", Static)
        self.bible = self.query_one("#bible", RichLog)
        self.bible.write("[b]CANON / DECISIONS[/]")
        self.channel.write("[b]🐝 universal editor[/] — confirm or edit the source, then press Enter.")
        self.query_one("#prompt", Input).focus()

    # ---- roster ----
    def _render_roster(self) -> None:
        lines = ["[b]🏛  Arbiter[/b]", ""]
        for sid in self.segments:
            lines.append(f"{GLYPH.get(self.state.get(sid, 'idle'), '⚪')}  {sid}")
        self.roster.update("\n".join(lines))

    # ---- event consumer (runs on UI thread via call_from_thread) ----
    def _on_event(self, ev: Event) -> None:
        k, p = ev.kind, ev.payload
        if k == E.PHASE:
            self.channel.write(f"\n[b on grey30] PHASE: {p['phase'].upper()} [/]")
        elif k == E.SEGMENTS:
            self.segments = list(p["segments"])
            self.state = {s: "idle" for s in self.segments}
            self._render_roster()
            self.channel.write(f"[dim]{len(self.segments)} segments[/]")
        elif k == E.ROUND:
            self.channel.write(f"\n[b]── round {p['round']}/{p['of']} ──[/]")
        elif k == E.MESSAGE:
            self.state[p["author"]] = "analyzing"
            self._render_roster()
            flag = " [green](stable)[/]" if p.get("stable") else ""
            self.channel.write(f"[cyan]{p['author']}[/]{flag} ▸ {p['text']}")
        elif k == E.PAIRING:
            self.channel.write(f"   [magenta]⇄ {p['a']} → {p['b']}[/] [dim]{p['topic']}[/]")
        elif k == E.DECISION:
            self.bible.write(f"❄ {p['text']}")
        elif k == E.ARBITER:
            self.channel.write(f"[b yellow]🏛 Arbiter[/] ▸ [yellow]{p['text']}[/]")
        elif k == E.PLAN:
            self.channel.write(f"[b green]✓ plan ready[/] [dim]{len(p['directives'])} directives[/]")
        elif k == E.MUTATION:
            if p.get("state") == "start":
                self.state[p["segment"]] = "mutating"
            else:
                self.state[p["segment"]] = "passed" if p.get("ok") else "failed"
                self.channel.write(f"[yellow]✎ {p['segment']}[/] [dim]{p.get('summary', '')}[/]")
            self._render_roster()
        elif k == E.VALIDATION:
            if not p.get("ok"):
                self.channel.write(f"[red]✗ {p['segment']} validation: {p.get('issues')}[/]")

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
            self.channel.write(f"[green]goal:[/] {value}\n[dim]starting engine…[/]")
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
            self.call_from_thread(
                self.channel.write,
                f"\n[b green]DONE[/] — wrote {len(final)} chars to {OUT}")
        except Exception as exc:
            self.call_from_thread(self.channel.write, f"\n[red]error:[/] {exc}")


def main() -> None:
    EngineApp().run()


if __name__ == "__main__":
    main()
