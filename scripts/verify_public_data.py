#!/usr/bin/env python3
"""Fail (exit 1) if data.json (or given files) break the public allowlist."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from public_schema import validate  # noqa: E402

files = [Path(p) for p in sys.argv[1:]] or [ROOT / "data.json"]
bad = 0
for f in files:
    errs = validate(json.loads(f.read_text()))
    for e in errs:
        print(f"{f}: {e}")
    bad += len(errs)
    if not errs:
        print(f"{f}: OK (allowlist)")
sys.exit(1 if bad else 0)
