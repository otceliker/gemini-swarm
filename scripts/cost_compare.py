"""Compare the SAME segmentation task on two surfaces:

  (A) the managed Antigravity agent (interactions API)  -> carries the IDE harness
  (B) the raw gemini-3-5-flash model (generate_content)  -> no harness

Proves lever #1: reasoning belongs on the raw model, not the agent.
Reuses the topology.json already produced for Flask.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.agents.architect import (  # noqa: E402
    PROMPT_TEMPLATE, SYSTEM_INSTRUCTION, _extract_json, digest_topology,
)
from swarm.mapping.ast_mapper import map_repository  # noqa: E402
from swarm.protocol.models import Topology  # noqa: E402

TOPO = Path(".workspaces/flask/topology.json")
CANDIDATE_MODELS = ["gemini-3-5-flash", "gemini-flash-latest",
                    "gemini-3-5-flash-preview", "models/gemini-3-5-flash"]


def load_topology() -> Topology:
    if TOPO.exists():
        return Topology.from_dict(json.loads(TOPO.read_text()))
    return map_repository(Path(".workspaces/flask/src/flask"))


def main() -> int:
    topo = load_topology()
    prompt = PROMPT_TEMPLATE.format(digest=digest_topology(topo))
    approx = len(SYSTEM_INSTRUCTION + prompt) // 4
    print(f"prompt+system ≈ {approx} tokens (chars/4 estimate)\n")

    client = genai.Client()
    last_err = None
    for model in CANDIDATE_MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
            )
        except Exception as e:  # try the next id
            last_err = e
            continue

        u = resp.usage_metadata
        print(f"model: {model}")
        print(f"  prompt_token_count    : {getattr(u, 'prompt_token_count', '?')}")
        print(f"  candidates_token_count: {getattr(u, 'candidates_token_count', '?')}")
        print(f"  thoughts_token_count  : {getattr(u, 'thoughts_token_count', None)}")
        print(f"  cached_content_tokens : {getattr(u, 'cached_content_token_count', None)}")
        print(f"  total_token_count     : {getattr(u, 'total_token_count', '?')}")
        try:
            data = _extract_json(resp.text)
            n = len(data["domains"]) if isinstance(data, dict) else len(data)
            print(f"  parsed domains        : {n}")
        except Exception as e:
            print(f"  (could not parse domains: {e})")
        print("\nvs managed agent earlier: prompt 97,816 / total 106,919 tokens")
        return 0

    print(f"FAIL: none of {CANDIDATE_MODELS} worked. last error: {last_err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
