"""Deterministic dependency-graph partitioning — the structural half of segmentation.

Grouping modules into cohesive, loosely-coupled domains *is* graph community
detection: maximize intra-group import edges, minimize cross-group ones (modularity).
This runs Louvain on the internal-dependency graph — deterministic (seeded), free,
and reproducible. The Architect only adds names/rationales on top of these clusters.
"""
from __future__ import annotations

import networkx as nx

from ..protocol.models import Topology


def _assignable(topology: Topology):
    # Modules with real content. Exclude the root package __init__ (empty module
    # name): it re-exports everything and would act as an all-connecting hub that
    # collapses every domain into one blob.
    return [m for m in topology.modules if m.module_name and (m.symbols or m.internal_deps)]


def build_graph(topology: Topology) -> "nx.Graph":
    mods = _assignable(topology)
    names = {m.module_name for m in mods}
    g = nx.Graph()
    g.add_nodes_from(names)
    for m in mods:
        for dep in m.internal_deps:
            if dep in names and dep != m.module_name:
                if g.has_edge(m.module_name, dep):
                    g[m.module_name][dep]["weight"] += 1
                else:
                    g.add_edge(m.module_name, dep, weight=1)
    return g


def _top(name: str) -> str:
    return name.split(".")[0]


def _edges_between(g: "nx.Graph", a: set, b: set) -> int:
    return sum(g[u][v].get("weight", 1) for u in a for v in b if g.has_edge(u, v))


def partition_modules(topology: Topology, *, resolution: float = 1.0, min_size: int = 2,
                      max_domains: int = 8, seed: int = 42) -> list[list[str]]:
    """Return a deterministic list of module-name clusters (largest first)."""
    g = build_graph(topology)
    if g.number_of_nodes() == 0:
        return []

    clusters = [set(c) for c in nx.community.louvain_communities(
        g, weight="weight", resolution=resolution, seed=seed)]

    # Fold small/singleton clusters into their best-connected neighbour
    # (tie-break: shared top-level package, then larger cluster).
    merged = True
    while merged and len(clusters) > 1:
        merged = False
        clusters.sort(key=lambda c: (len(c), sorted(c)))
        for i, c in enumerate(clusters):
            if len(c) >= min_size:
                continue
            others = [(j, o) for j, o in enumerate(clusters) if j != i]

            def score(item):
                _, o = item
                shared = 1 if {_top(x) for x in c} & {_top(y) for y in o} else 0
                return (_edges_between(g, c, o), shared, len(o))

            _, target = max(others, key=score)
            target |= c
            clusters.pop(i)
            merged = True
            break

    # Cap the number of domains by repeatedly merging the most-connected pair.
    while len(clusters) > max_domains:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                e = _edges_between(g, clusters[i], clusters[j])
                if best is None or e > best[0]:
                    best = (e, i, j)
        _, i, j = best
        clusters[i] |= clusters[j]
        clusters.pop(j)

    return sorted((sorted(c) for c in clusters), key=lambda c: (-len(c), c[0]))
