"""Independently verify the FastAPI Lead's claimed change.

Resumes the SAME sandbox environment the modification ran in (files persist),
shows the real git diff, and RE-RUNS the relevant tests ourselves so a
self-reported PASS can't slip through unchecked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from google import genai

AGENT = "antigravity-preview-05-2026"
ENV = "cc2b94d5-1391-40bf-bcc6-deebc794fc1a"  # the FastAPI modification workspace

PROMPT = r"""Run these commands in the existing /workspace/repo checkout and paste their RAW output verbatim.
Do not edit anything; this is read-only verification.

1) git -C /workspace/repo status --short
2) git -C /workspace/repo diff
3) grep -rn "deprecated_reason" /workspace/repo/fastapi | head -50
4) Re-run ONLY the parameter-related tests fresh, e.g.:
   cd /workspace/repo && python -m pytest tests/test_params_repr.py tests/test_application.py -q 2>&1 | tail -25
   (If those paths don't exist, find and run the most relevant existing param tests and any new test you added.)

Paste the literal output of each. End with one line: VERIFIED_DIFF_PRESENT=<true/false> TESTS_PASS=<true/false>
"""


def main() -> int:
    client = genai.Client()
    i = client.interactions.create(
        agent=AGENT, input=PROMPT, environment=ENV,
        tools=[{"type": "code_execution"}],
    )
    print("environment_id:", i.environment_id)
    print("---- OUTPUT ----")
    print(i.output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
