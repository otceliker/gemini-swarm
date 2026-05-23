# gemini-swarm

A graph-based multi-agent swarm that reads, reports on, and autonomously modifies
Python codebases — built on **Google Antigravity Managed Agents** (Gemini 3.5 Flash).

An **Architect** maps a repo into a dependency graph, segments it into domains, and
plans changes. **Domain Lead** agents mutate code and run `pytest` inside persistent
sandboxes. All cross-agent coordination is routed through the Architect (no
peer-to-peer chatter), which serializes edits and propagates contract changes.

## Design

- **Two execution paths.** Planning/segmentation run on the raw model
  (`gemini-flash-latest`) via `generate_content` — cheap, no sandbox. Code mutation
  runs on the managed agent (`antigravity-preview-05-2026`) in a persistent Linux
  sandbox with `code_execution`. (The managed-agent harness adds a ~90k-token floor
  per turn, so reasoning deliberately avoids it.)
- **Deterministic first.** A stdlib `ast` pass builds `topology.json` (modules,
  signatures, internal dependency edges) before any LLM reasoning.
- **Serialized v0.** One shared sandbox, one mutating domain at a time — no merge
  conflicts. Parallel per-domain sandboxes come later.

## Usage

```bash
python -m venv .venv && ./.venv/bin/pip install -e .
export GEMINI_API_KEY=...

# report-only: clone, map, segment
python -m swarm https://github.com/fastapi/fastapi --subdir fastapi

# map only, no LLM
python -m swarm <url> --offline

# autonomous modification
python -m swarm <url> --subdir fastapi --task "add an optional X param to Y"
```

## Layout

```
swarm/
  mapping/ast_mapper.py   deterministic repo → topology.json
  agents/architect.py     segmentation, planning, contract propagation (reasoning path)
  agents/lead.py          Domain Lead sandbox executor (managed-agent path)
  agents/backend.py       AgentBackend + Reasoner (real + fakes)
  orchestrator.py         serialized loop + propagation + cycle guard
  cli.py                  python -m swarm entrypoint
tests/                    offline tests (FakeBackend / FakeReasoner)
scripts/                  smoke + verification probes
```

Status: v0 working and verified end-to-end on `fastapi/fastapi`. Not yet built:
interactive approval gate, the Textual TUI, parallel multi-sandbox execution.
