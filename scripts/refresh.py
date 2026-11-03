#!/usr/bin/env python3
"""GitHub Actions fetcher. Two sub-commands so the rotated refresh token can be
saved (step 2 of the workflow) BEFORE any data call can fail.

  refresh.py token --out-dir DIR
      env TESLA_CLIENT_ID, TESLA_REFRESH_TOKEN
      -> DIR/access_token, DIR/refresh_token (mode 600), both ::add-mask::'d

  refresh.py fetch --token-dir DIR --out _site/data.json [--previous prev.json]
      env TESLA_SITE_ID, TESLA_SITE_TZ, TESLA_API_BASE (optional)
      -> allowlisted + validated public snapshot

Never prints tokens, the site id, the timezone or raw API responses. The logs of a
public repo are public.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import powerwall_core as core  # noqa: E402
from public_schema import validate  # noqa: E402


def _env(name: str, required: bool = True) -> str:
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        raise SystemExit(f"missing required secret/env {name}")
    return v


def _write_secret_file(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def cmd_token(args) -> None:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    access, new_refresh = core.refresh_access_token(_env("TESLA_CLIENT_ID"), _env("TESLA_REFRESH_TOKEN"))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Mask before anything else could possibly print them.
        print(f"::add-mask::{access}")
        print(f"::add-mask::{new_refresh}")
    _write_secret_file(out / "access_token", access)
    _write_secret_file(out / "refresh_token", new_refresh)
    print("token refreshed" + (" (rotated)" if new_refresh != os.environ.get("TESLA_REFRESH_TOKEN", "").strip() else ""))


def cmd_fetch(args) -> None:
    access = Path(args.token_dir, "access_token").read_text().strip()
    previous = None
    if args.previous and Path(args.previous).is_file():
        try:
            previous = json.loads(Path(args.previous).read_text())
            if not isinstance(previous, dict) or validate(previous):
                previous = None  # ignore anything that isn't a valid public snapshot
        except ValueError:
            previous = None
    data = core.fetch_snapshot(
        api_base=_env("TESLA_API_BASE", required=False) or core.DEFAULT_API_BASE,
        access=access,
        site_id=_env("TESLA_SITE_ID"),
        tz_name=_env("TESLA_SITE_TZ"),
        previous=previous,
    )
    errors = validate(data)
    if errors:
        # Paths only, no values.
        raise SystemExit("public data failed validation: " + "; ".join(e.split(":")[0] for e in errors[:10]))
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")))
    print(f"snapshot ok: {len(data['day']['points'])} day points, {len(data['week']['days'])} week days")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token"); t.add_argument("--out-dir", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--token-dir", required=True)
    f.add_argument("--out", required=True); f.add_argument("--previous")
    args = ap.parse_args(argv)
    try:
        {"token": cmd_token, "fetch": cmd_fetch}[args.cmd](args)
    except core.TeslaError as e:  # message contains only HTTP status + Tesla error code
        raise SystemExit(f"Tesla API error: {e}")


if __name__ == "__main__":
    main()
