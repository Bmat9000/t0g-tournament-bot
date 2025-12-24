# core/config.py
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env exactly once (this module should be imported early by main.py)
load_dotenv()

# ---------- Project Paths ----------
# core/ sits at: <ROOT>/core/config.py
ROOT: Path = Path(__file__).resolve().parents[1]

DATA_DIR: Path = ROOT / "data"
ASSETS_DIR: Path = ROOT / "assets"

# DB path can be overridden by env var while keeping the same default behavior
# (default remains ./data/tournaments.db)
DB_PATH: Path = Path(os.getenv("DB_PATH", str(DATA_DIR / "tournaments.db"))).resolve()

# ---------- Ensure folders exist (safe/no behavior change) ----------
DATA_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# ---------- Environment Helpers ----------
def env(key: str, default: str | None = None) -> str | None:
    """Small helper to read env vars consistently."""
    return os.getenv(key, default)

def env_bool(key: str, default: bool = False) -> bool:
    """Parse bool-like env vars."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")
