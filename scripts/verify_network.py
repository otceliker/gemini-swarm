"""Hard verification of network egress in a FRESH managed-agent sandbox.

Spins up a brand-new remote environment and runs commands whose output is hard
to fabricate (live DNS, HTTP status/timing, current GitHub star count + push
time, a real pip download). We then cross-check the values for plausibility.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from google import genai

AGENT = "antigravity-preview-05-2026"

PROMPT = r"""Run EACH of these shell commands and report their RAW, unmodified output.
Do NOT summarize, edit, or invent any output — paste exactly what the shell prints.

1) python3 -c "import socket; print(socket.gethostbyname('pypi.org'))"
2) curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s" https://pypi.org/simple/
3) curl -s https://api.github.com/repos/fastapi/fastapi | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['stargazers_count'], d['pushed_at'])"
4) pip download --no-deps requests -d /tmp/netcheck 2>&1 | tail -2
5) date -u

Then respond with ONLY this JSON (values = literal raw output of each command):
{"dns_ip": "...", "http": "...", "github_stars_pushed": "...", "pip": "...", "date_utc": "...", "network_available": true}
"""


def main() -> int:
    client = genai.Client()
    i = client.interactions.create(
        agent=AGENT, input=PROMPT, environment="remote",
        tools=[{"type": "code_execution"}],
    )
    print("environment_id:", i.environment_id)
    print("interaction_id:", i.id)
    print("---- OUTPUT ----")
    print(i.output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
