#!/usr/bin/env python3
"""Scan the repo for personal-info / secret patterns. Exit 1 on any hit.

Run before every commit/deploy:  python3 scripts/scan_repo.py
Skips: .git, the vendored (sha256-pinned) Chart.js, and gitignored local files.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", "dist", "_site"}
SKIP_FILES = {"vendor/chart.umd.min.js", ".env", ".tesla-local.json", "scripts/scan_repo.py"}
SKIP_EXT = {".png", ".jpg", ".zip", ".ico"}

PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<![\w.:-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.-])"),
    "VIN": re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b(?<=\b(?=[^a-z]*\d)[A-HJ-NPR-Z0-9]{17})"),
    "Tesla DIN/serial": re.compile(r"\b\d{7}-\d{2}-[A-Z]--[A-Z0-9]{8,}\b"),
    "long numeric id": re.compile(r"(?<![\d.])\d{12,}(?![\d.])"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    "Tesla token": re.compile(r"\b(?:NA|EU|CN)_[0-9a-f]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:EC |RSA )?PRIVATE KEY-----"),
    "AWS key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "lat/lon pair": re.compile(r"-?\d{1,2}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}"),
    "street address": re.compile(r"\b\d{2,6}\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Blvd|Way)\b"),
    "IANA timezone": re.compile(r"\b(?:America|Europe|Asia|Australia|Africa|Pacific)/[A-Z][A-Za-z_]+"),
    "AWS account ARN": re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:"),
}
# Placeholders used deliberately in docs.
ALLOW = [re.compile(p) for p in (r"111122223333", r"ACCOUNT_ID", r"example\.(?:com|org)")]

hits = 0
for f in sorted(ROOT.rglob("*")):
    rel = f.relative_to(ROOT).as_posix()
    if not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(ROOT).parts):
        continue
    if rel in SKIP_FILES or f.suffix in SKIP_EXT:
        continue
    try:
        text = f.read_text(errors="ignore")
    except Exception:
        continue
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in PATTERNS.items():
            for m in rx.finditer(line):
                if any(a.search(m.group(0)) for a in ALLOW):
                    continue
                print(f"{rel}:{n}: possible {name}: {m.group(0)[:60]}")
                hits += 1
print("scan: clean" if not hits else f"scan: {hits} potential issue(s)")
sys.exit(1 if hits else 0)
