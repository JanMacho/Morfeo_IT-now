"""Where the lab lives on disk. Templates, images and configuration are read from the lab folder."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
DEPLOY = ROOT / "deploy"
STATE_FILE = ROOT / ".morfeo-state.json"


def rel(p: Path | str) -> str:
    """A path for messages: relative to the current folder when it is below it, with the separators of
    this OS, so that it can be pasted into the terminal or the file explorer."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def lab_config_path() -> Path:
    return Path(os.environ.get("MORFEO_LAB_CONFIG") or ROOT / "config" / "lab.yaml")
