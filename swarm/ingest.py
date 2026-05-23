"""Phase 1 ingestion: shallow-clone a public repo into an isolated workspace."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

WORKSPACE_ROOT = Path(".workspaces")


def _repo_slug(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    name = re.sub(r"\.git$", "", name)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "repo"


def clone_repo(url: str, workspace_root: Path | str = WORKSPACE_ROOT,
               *, reuse: bool = True) -> Path:
    """`git clone --depth 1` into `<workspace_root>/<slug>`.

    Reuses an existing clone by default so repeated runs don't re-download.
    """
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    dest = workspace_root / _repo_slug(url)

    if dest.exists():
        if reuse and (dest / ".git").exists():
            return dest
        raise FileExistsError(f"{dest} already exists; pass reuse=False to overwrite manually")

    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest
