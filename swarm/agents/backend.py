"""Agent backend abstraction over Google Antigravity Managed Agents.

The whole swarm talks to agents through `AgentBackend` so the orchestration logic
(mapping, segmentation, propagation) stays testable offline via `FakeBackend`,
while `ManagedAgentBackend` drives the real `google-genai` interactions API.

Validated against google-genai 2.6.0 / antigravity-preview-05-2026:
  - interactions.create(agent, input, environment="remote"|<env_id>, tools, system_instruction)
  - response exposes .id, .environment_id, .output_text, .usage
  - resume by passing previous_interaction_id + environment=<env_id>
  - .steps emits pydantic serialization warnings in 2.6.0, so we don't depend on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

AGENT_ID = "antigravity-preview-05-2026"
# v0 restricts agents to code execution (shell/python/node); the system instruction
# steers them to use it only for pytest + file edits, per the spec.
DEFAULT_TOOLS: list[dict[str, str]] = [{"type": "code_execution"}]


@dataclass
class TurnResult:
    """A single agent turn. `interaction_id` + `environment_id` are the resume handles."""

    text: str
    interaction_id: str
    environment_id: str
    usage: dict[str, Any] | None = None


class AgentBackend(Protocol):
    def start(self, system_instruction: str, prompt: str, *,
              environment: str = "remote", tools: list[dict] | None = None) -> TurnResult: ...

    def resume(self, interaction_id: str, environment_id: str, prompt: str, *,
               tools: list[dict] | None = None) -> TurnResult: ...


class ManagedAgentBackend:
    """Real backend: one persistent server-side Linux sandbox, resumable across turns."""

    def __init__(self, agent_id: str = AGENT_ID, client: Any = None):
        self.agent_id = agent_id
        if client is None:
            from google import genai  # imported lazily so offline tests need no key
            client = genai.Client()
        self.client = client

    def start(self, system_instruction, prompt, *, environment="remote", tools=None):
        i = self.client.interactions.create(
            agent=self.agent_id, input=prompt, environment=environment,
            system_instruction=system_instruction, tools=tools or DEFAULT_TOOLS,
        )
        return self._wrap(i)

    def resume(self, interaction_id, environment_id, prompt, *, tools=None):
        i = self.client.interactions.create(
            agent=self.agent_id, previous_interaction_id=interaction_id,
            environment=environment_id, input=prompt, tools=tools or DEFAULT_TOOLS,
        )
        return self._wrap(i)

    @staticmethod
    def _wrap(i: Any) -> TurnResult:
        usage = getattr(i, "usage", None)
        if usage is not None and hasattr(usage, "model_dump"):
            try:
                usage = usage.model_dump()
            except Exception:
                usage = None
        return TurnResult(
            text=getattr(i, "output_text", "") or "",
            interaction_id=getattr(i, "id", "") or "",
            environment_id=getattr(i, "environment_id", "") or "",
            usage=usage,
        )


@dataclass
class FakeBackend:
    """Scripted backend for offline tests of orchestration logic.

    `responses` is consumed in order; each call returns the next text. Records every
    prompt so tests can assert on what the orchestrator sent.
    """

    responses: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    _i: int = 0
    _env: str = "fake-env-0001"

    def _next(self, prompt: str) -> TurnResult:
        self.prompts.append(prompt)
        text = self.responses[self._i] if self._i < len(self.responses) else ""
        self._i += 1
        return TurnResult(text=text, interaction_id=f"fake-int-{self._i}", environment_id=self._env)

    def start(self, system_instruction, prompt, *, environment="remote", tools=None):
        return self._next(prompt)

    def resume(self, interaction_id, environment_id, prompt, *, tools=None):
        return self._next(prompt)


# --- Reasoning path -------------------------------------------------------
# Planning/segmentation/validation don't need a sandbox, so they run on the raw
# model via generate_content — which sheds the ~92k-token Antigravity agent
# harness floor (measured ~37x cheaper input than a managed-agent turn).

REASONING_MODEL = "gemini-flash-latest"  # `gemini-3-5-flash` does not resolve for generate_content


class Reasoner(Protocol):
    def complete(self, system_instruction: str, prompt: str) -> str: ...


class GeminiReasoner:
    """Raw-model reasoning via generate_content. No sandbox, no harness."""

    def __init__(self, model: str = REASONING_MODEL, client: Any = None):
        self.model = model
        if client is None:
            from google import genai
            client = genai.Client()
        self.client = client

    def complete(self, system_instruction: str, prompt: str) -> str:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction, temperature=0.0),
        )
        return resp.text or ""


@dataclass
class FakeReasoner:
    """Scripted reasoner for offline tests."""

    responses: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    _i: int = 0

    def complete(self, system_instruction: str, prompt: str) -> str:
        self.prompts.append(prompt)
        text = self.responses[self._i] if self._i < len(self.responses) else ""
        self._i += 1
        return text
