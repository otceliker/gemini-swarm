"""`python -m swarm <repo-url>` — clone, map, segment, and (optionally) modify.

  python -m swarm <url> [--subdir src/pkg]              # report only
  python -m swarm <url> --offline                        # map only, no LLM
  python -m swarm <url> --task "add X to Y"              # autonomous modification
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from .agents.architect import Architect
from .agents.backend import GeminiReasoner, ManagedAgentBackend
from .ingest import clone_repo
from .mapping.ast_mapper import map_repository, write_topology
from .orchestrator import Orchestrator


def _domains_table(domains, unassigned) -> Table:
    table = Table(title="Proposed Domains", show_lines=True)
    table.add_column("Domain", style="bold cyan")
    table.add_column("Modules", style="white")
    table.add_column("Rationale", style="dim")
    for d in domains:
        table.add_row(d.name, "\n".join(d.module_names) or "[dim]—[/]", d.rationale)
    return table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarm")
    parser.add_argument("repo_url", help="public GitHub repository URL")
    parser.add_argument("--subdir", default=None, help="only map this subdirectory (e.g. fastapi)")
    parser.add_argument("--offline", action="store_true", help="map only; skip the Architect LLM")
    parser.add_argument("--task", default=None, help="run the autonomous modification loop with this intent")
    args = parser.parse_args(argv)
    console = Console()

    console.rule("[bold]Phase 1: Ingestion & Mapping")
    repo_path = clone_repo(args.repo_url)
    map_root = repo_path / args.subdir if args.subdir else repo_path
    topology = map_repository(map_root)
    topo_file = write_topology(topology, repo_path / "topology.json")
    console.print(f"Cloned [cyan]{args.repo_url}[/] → [dim]{repo_path}[/]")
    console.print(f"Mapped [bold]{len(topology.modules)}[/] modules → [dim]{topo_file}[/]")

    if args.offline:
        for m in topology.modules:
            if m.internal_deps:
                console.print(f"  {m.module_name} [dim]→ {', '.join(m.internal_deps)}[/]")
        return 0

    console.rule("[bold]Phase 1.4: Architect Segmentation")
    architect = Architect(GeminiReasoner())
    with console.status("Architect is partitioning the codebase…"):
        seg = architect.segment(topology)
    console.print(_domains_table(seg.domains, seg.unassigned))
    if seg.unassigned:
        console.print(f"[yellow]Unassigned (→ Architect/common):[/] {', '.join(seg.unassigned)}")

    if not args.task:
        return 0

    console.rule("[bold]Phase 4: Serialized Modification Loop")
    console.print(f"Intent: [italic]{args.task}[/]")
    orch = Orchestrator(architect, ManagedAgentBackend(), repo_url=args.repo_url,
                        subdir=args.subdir or "")
    with console.status("Swarm is working (planning → bootstrap → serialized leads)…"):
        run = orch.run(topology, seg.domains, args.task)

    console.print(f"[dim]baseline:[/] {run.baseline[:200]}")
    results = Table(title="Lead Reports", show_lines=True)
    results.add_column("Domain", style="bold cyan")
    results.add_column("Kind", style="dim")
    results.add_column("Tests", justify="center")
    results.add_column("Summary", style="white")
    kinds = {d.domain: d.kind for d in run.directives}
    for r in run.reports:
        mark = "[green]✓[/]" if r.tests_passed else "[red]✗[/]"
        results.add_row(r.domain, kinds.get(r.domain, "propagation"), mark, r.summary or "[dim]—[/]")
    console.print(results)
    console.print(f"[dim]workspace env: {run.environment_id}[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
