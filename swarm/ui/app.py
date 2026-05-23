"""Textual TUI for gemini-swarm.

Flow: enter a GitHub URL -> live clone/map/segment streamed into the conversation
-> the swarm roster fills in -> chat with the Architect. Blocking calls (git, LLM)
run in worker threads so the UI stays responsive.

Run:  python -m swarm.ui
"""
from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, RichLog, Static

from ..agents.architect import Architect
from ..agents.backend import GeminiReasoner

GLYPH = {"idle": "⚪", "analyzing": "🔵", "mutating": "🟡", "passed": "🟢", "failed": "🔴"}


class SwarmApp(App):
    TITLE = "gemini-swarm"
    CSS = """
    #body { height: 1fr; }
    #roster {
        width: 34;
        border-right: solid $accent;
        padding: 1 2;
    }
    #convo { padding: 0 1; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.phase = "url"            # url -> running -> chat
        self.architect: Architect | None = None
        self.topology = None
        self.domains: list = []
        self.roster_state: dict[str, str] = {}
        self.repo_url = ""
        self.subdir = ""
        self.chat_history: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Static("🏛  Architect", id="roster")
            yield RichLog(id="convo", markup=True, wrap=True)
        yield Input(placeholder="GitHub repo URL   (optionally:  <url> <subdir>)", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.convo = self.query_one("#convo", RichLog)
        self.roster = self.query_one("#roster", Static)
        self.convo.write("[b]🐝 gemini-swarm[/b]")
        self.convo.write("Enter a public GitHub repo URL to begin "
                         "(e.g. [cyan]https://github.com/fastapi/fastapi fastapi[/]).")
        self.query_one("#prompt", Input).focus()

    # ---- UI helpers (main thread only) ----
    def say(self, msg: str) -> None:
        self.convo.write(msg)

    def render_roster(self) -> None:
        lines = ["[b]🏛  Architect[/b]", ""]
        for d in self.domains:
            glyph = GLYPH.get(self.roster_state.get(d.name, "idle"), "⚪")
            lines.append(f"{glyph}  {d.name}")
        self.roster.update("\n".join(lines))

    # ---- input ----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        event.input.value = ""
        if self.phase == "url":
            parts = value.split()
            url, subdir = parts[0], (parts[1] if len(parts) > 1 else "")
            self.phase = "running"
            event.input.disabled = True
            event.input.placeholder = "working…"
            self.say(f"\n[dim]▸[/] cloning [cyan]{url}[/]…")
            self.run_pipeline(url, subdir)
        elif self.phase == "chat":
            self.say(f"\n[b]you ▸[/] {value}")
            self.ask_architect(value)

    # ---- workers (threads) ----
    @work(thread=True)
    def run_pipeline(self, url: str, subdir: str) -> None:
        from ..ingest import clone_repo
        from ..mapping.ast_mapper import map_repository
        self.repo_url, self.subdir = url, subdir
        try:
            path = clone_repo(url)
            self.call_from_thread(self.say, f"[green]✓[/] cloned → [dim]{path}[/]")
            root = path / subdir if subdir else path
            topo = map_repository(root)
            self.call_from_thread(self.say, f"[green]✓[/] mapped [b]{len(topo.modules)}[/] modules")
            self.call_from_thread(self.say, "[dim]🏛  Architect segmenting…[/]")
            architect = Architect(GeminiReasoner())
            seg = architect.segment(topo)
            self.architect, self.topology, self.domains = architect, topo, seg.domains
            self.call_from_thread(self._on_segmented, seg)
        except Exception as exc:  # surface, don't crash the UI
            self.call_from_thread(self.say, f"[red]error:[/] {exc}")
            self.call_from_thread(self._reset_to_url)

    def _on_segmented(self, seg) -> None:
        self.roster_state = {d.name: "idle" for d in seg.domains}
        self.render_roster()
        self.say(f"[green]✓[/] Architect split the codebase into [b]{len(seg.domains)}[/] domains:")
        for d in seg.domains:
            self.say(f"  [b cyan]{d.name}[/] — [dim]{d.rationale}[/]")
        if seg.unassigned:
            self.say(f"  [yellow]unassigned → common:[/] {', '.join(seg.unassigned)}")
        self.phase = "chat"
        inp = self.query_one("#prompt", Input)
        inp.disabled = False
        inp.placeholder = "chat with the Architect…   (@domain coming soon)"
        inp.focus()

    def _reset_to_url(self) -> None:
        inp = self.query_one("#prompt", Input)
        inp.disabled = False
        inp.placeholder = "GitHub repo URL"
        self.phase = "url"

    def _grounding(self) -> str:
        from ..agents.architect import digest_topology
        lines = [f"Repository (ground truth): {self.repo_url}"]
        if self.subdir:
            lines.append(f"Mapped package/subdir: {self.subdir}")
        lines.append("\nDomains:")
        for d in self.domains:
            lines.append(f"- {d.name}: {d.rationale}")
            lines.append(f"    modules: {', '.join(d.module_names)}")
        lines.append("\nDependency map (module | internal deps | public symbols):")
        lines.append(digest_topology(self.topology))
        return "\n".join(lines)

    @work(thread=True)
    def ask_architect(self, message: str) -> None:
        system = (
            "You are the Architect of a multi-agent code swarm that just analyzed the "
            "repository described below. Answer questions about THIS specific repository, "
            "treating the repo URL and dependency map as ground truth — do not guess what "
            "project it is, you are told. Be concise and concrete."
        )
        history = "\n".join(self.chat_history[-12:])
        context = (f"{self._grounding()}\n\n--- conversation so far ---\n"
                   f"{history}\nUser: {message}\nArchitect:")
        try:
            reply = (self.architect.reasoner.complete(system, context) or "").strip()
        except Exception as exc:
            reply = f"[error] {exc}"
        self.chat_history.append(f"User: {message}")
        self.chat_history.append(f"Architect: {reply}")
        self.call_from_thread(self.say, f"[b]🏛  Architect ▸[/] {reply}")


def main() -> None:
    SwarmApp().run()


if __name__ == "__main__":
    main()
