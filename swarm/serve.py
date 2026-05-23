"""Serve a swarm TUI in the browser via textual-serve directly.

Avoids the `textual serve` CLI's path-detection bug (it tries to read the python
binary as a source file). Run from the repo root:

    ./.venv/bin/python -m swarm.serve            # -> http://localhost:8000
    ./.venv/bin/python -m swarm.serve --module swarm.ui.app   # the code-path TUI
"""
from __future__ import annotations

import argparse
import sys

from textual_serve.server import Server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swarm-serve")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--module", default="swarm.ui.engine_app",
                    help="module launched as `python -m <module>` per browser session")
    args = ap.parse_args(argv)

    command = f"{sys.executable} -m {args.module}"
    print(f"serving `{command}`\n  open http://{args.host}:{args.port}  (ctrl+c to stop)")
    Server(command, host=args.host, port=args.port, title="gemini-swarm").serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
