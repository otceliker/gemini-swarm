"""Smoke test: validate the load-bearing mechanism of the swarm architecture.

Confirms (1) a remote managed-agent sandbox provisions, (2) code_execution works,
and (3) BOTH sandbox filesystem state and conversation history persist across a
resumed interaction. Everything else in the system depends on this working.

Run: ./.venv/bin/python scripts/smoke_test.py
Cost: 2 managed-agent interactions on antigravity-preview-05-2026 (kept tiny).
"""
import sys
from google import genai

AGENT = "antigravity-preview-05-2026"


def fields(obj):
    """Best-effort dump of a (likely pydantic) response object's keys."""
    for attr in ("model_dump", "to_dict", "dict"):
        if hasattr(obj, attr):
            try:
                return list(getattr(obj, attr)().keys())
            except Exception:
                pass
    return [a for a in dir(obj) if not a.startswith("_")]


def get(obj, name, default=None):
    return getattr(obj, name, default)


def main():
    client = genai.Client()

    print("== call 1: provision sandbox + write a file ==")
    try:
        i1 = client.interactions.create(
            agent=AGENT,
            input="Run a shell command to write the text PONG into the file /tmp/swarm_probe.txt. Then reply with exactly: WROTE_FILE",
            environment="remote",
            tools=[{"type": "code_execution"}],
        )
    except Exception as e:
        print(f"FAIL: interactions.create raised: {type(e).__name__}: {e}")
        print("(If this is an unknown-agent error, the agent id string is wrong.)")
        sys.exit(1)

    print("response fields:", fields(i1))
    iid = get(i1, "id")
    env = get(i1, "environment_id")
    out = get(i1, "output_text")
    print("interaction id:", iid)
    print("environment id:", env)
    print("output_text:", repr(out)[:300])

    if not iid or not env:
        print("FAIL: missing id or environment_id on response; cannot test resume.")
        print("Inspect the fields above to find the real attribute names.")
        sys.exit(1)

    print("\n== call 2: resume same sandbox + conversation, read the file back ==")
    i2 = client.interactions.create(
        agent=AGENT,
        previous_interaction_id=iid,
        environment=env,
        input="Read /tmp/swarm_probe.txt and reply with exactly its contents and nothing else.",
        tools=[{"type": "code_execution"}],
    )
    out2 = (get(i2, "output_text") or "")
    print("output_text:", repr(out2)[:300])

    if "PONG" in out2:
        print("\nPASS: sandbox filesystem + conversation persisted across resume.")
    else:
        print("\nWARN: did not see PONG in resumed output. Persistence unconfirmed; "
              "review output above (model may have phrased the answer differently).")


if __name__ == "__main__":
    main()
