"""Tiny .env loader (no dependency). .env is gitignored; never commit it."""
import os
from pathlib import Path


def load_env(path: str | None = None) -> None:
    p = Path(path or Path(__file__).resolve().parent.parent / ".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Set {name} in .env or the environment")
    return v
