"""Core data models shared across the swarm.

These are deliberately plain dataclasses (no behavior) so they serialize cleanly
to/from JSON for `topology.json`, agent memory ledgers, and the contract messages
that Domain Leads return to the Architect.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Symbol:
    """A top-level function/class (or a class method) discovered by the mapper."""

    name: str                # qualified within its module, e.g. "LoginRouter.handle"
    kind: str                # "function" | "class" | "method"
    signature: str           # readable signature, e.g. "verify_jwt_token(token: str) -> bool"
    lineno: int
    is_public: bool          # part of the module's public surface (see Module.exports)


@dataclass
class Module:
    """One Python source file."""

    path: str                            # repo-relative posix path, e.g. "auth/service.py"
    module_name: str                     # dotted, e.g. "auth.service"
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)        # raw resolved import targets
    internal_deps: list[str] = field(default_factory=list)  # subset of imports that are internal modules
    exports: list[str] = field(default_factory=list)        # public names (__all__ or non-underscore)


@dataclass
class Topology:
    """The deterministic map of a repository, produced before any LLM reasoning."""

    root: str
    modules: list[Module] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Topology":
        modules = [
            Module(
                **{
                    **m,
                    "symbols": [Symbol(**s) for s in m.get("symbols", [])],
                }
            )
            for m in data.get("modules", [])
        ]
        return cls(root=data["root"], modules=modules)

    def module_names(self) -> list[str]:
        return [m.module_name for m in self.modules]


@dataclass
class Domain:
    """A logical slice of the codebase owned by one Domain Lead agent."""

    name: str
    module_names: list[str]
    rationale: str = ""


@dataclass
class ContractChange:
    """A proposed cross-boundary signature change.

    A Domain Lead returns this to the Architect (never directly to a peer); the
    Architect validates it against the global topology and dispatches propagation.
    Replaces the spec's peer-to-peer JSON-RPC mesh with a star transport.
    """

    target_module: str
    target_symbol: str
    proposed_signature: str
    reason: str


@dataclass
class DomainDirective:
    """A concrete unit of work the Architect hands to one Domain Lead."""

    domain: str
    instruction: str
    target_modules: list[str] = field(default_factory=list)
    kind: str = "primary"     # "primary" (from user intent) | "propagation" (from a contract change)
    origin: str = ""          # for propagation: "<module>.<symbol>" that triggered it


@dataclass
class LeadReport:
    """What a Domain Lead returns after working its directive in the sandbox."""

    domain: str
    success: bool
    summary: str = ""
    tests_passed: bool = False
    tests_output: str = ""
    contract_changes: list[ContractChange] = field(default_factory=list)
