"""Strict ALLOWLIST validator for the public data.json.

Every key at every level must be explicitly listed here, every string must
match a fixed harmless format, and numbers must be in a plausible range (large
integers that could be account/site IDs are rejected). Used by the Lambda
before publishing and by scripts/verify_public_data.py in CI/local checks.
"""
from __future__ import annotations

import math
import re

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM = re.compile(r"^\d{2}:\d{2}$")
UTC_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")  # UTC only, no offset

ENERGY_KEYS = {"solar_wh", "home_wh", "grid_import_wh", "grid_export_wh",
               "battery_charge_wh", "battery_discharge_wh"}
MAX_W = 1_000_000          # 1 MW: far above any home, far below ID-sized ints
MAX_WH = 100_000_000       # 100 MWh


def _num(path, v, lo, hi, errors, nullable=True):
    if v is None and nullable:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        errors.append(f"{path}: expected number, got {type(v).__name__}")
    elif not (lo <= v <= hi):
        errors.append(f"{path}: {v} out of range [{lo}, {hi}]")


def _keys(path, obj, allowed, errors, required=()):
    if not isinstance(obj, dict):
        errors.append(f"{path}: expected object")
        return False
    for k in obj:
        if k not in allowed:
            errors.append(f"{path}: key '{k}' is not allowlisted")
    for k in required:
        if k not in obj:
            errors.append(f"{path}: missing '{k}'")
    return True


def _str(path, v, rx, errors):
    if not isinstance(v, str) or not rx.match(v):
        errors.append(f"{path}: string does not match required format")


def validate(data) -> list[str]:
    e: list[str] = []
    if not _keys("$", data, {"schema", "example", "generated_at", "live", "day", "week"}, e,
                 required=("schema", "example", "generated_at", "live", "day", "week")):
        return e
    if data.get("schema") != 1:
        e.append("$.schema: must be 1")
    if not isinstance(data.get("example"), bool):
        e.append("$.example: must be boolean")
    _str("$.generated_at", data.get("generated_at"), UTC_TS, e)

    live = data.get("live")
    if _keys("$.live", live, {"solar_w", "home_w", "battery_w", "grid_w", "battery_pct", "grid_online"}, e):
        for k in ("solar_w", "home_w"):
            _num(f"$.live.{k}", live.get(k), -MAX_W, MAX_W, e)
        for k in ("battery_w", "grid_w"):
            _num(f"$.live.{k}", live.get(k), -MAX_W, MAX_W, e)
        _num("$.live.battery_pct", live.get("battery_pct"), 0, 100, e)
        go = live.get("grid_online")
        if go is not None and not isinstance(go, bool):
            e.append("$.live.grid_online: must be boolean or null")

    day = data.get("day")
    if _keys("$.day", day, {"date", "points", "totals", "battery_pct_trail"}, e):
        _str("$.day.date", day.get("date"), DATE, e)
        pts = day.get("points")
        if not isinstance(pts, list) or len(pts) > 300:
            e.append("$.day.points: must be a list (<=300)")
        else:
            for i, p in enumerate(pts):
                if _keys(f"$.day.points[{i}]", p, ENERGY_KEYS | {"t"}, e):
                    _str(f"$.day.points[{i}].t", p.get("t"), HHMM, e)
                    for k in ENERGY_KEYS:
                        _num(f"$.day.points[{i}].{k}", p.get(k), 0, MAX_WH, e)
        tot = day.get("totals")
        if _keys("$.day.totals", tot, ENERGY_KEYS, e):
            for k in ENERGY_KEYS:
                _num(f"$.day.totals.{k}", tot.get(k), 0, MAX_WH, e)
        trail = day.get("battery_pct_trail")
        if not isinstance(trail, list) or len(trail) > 300:
            e.append("$.day.battery_pct_trail: must be a list (<=300)")
        else:
            for i, p in enumerate(trail):
                if _keys(f"$.day.battery_pct_trail[{i}]", p, {"t", "pct"}, e, required=("t", "pct")):
                    _str(f"$.day.battery_pct_trail[{i}].t", p.get("t"), HHMM, e)
                    _num(f"$.day.battery_pct_trail[{i}].pct", p.get("pct"), 0, 100, e, nullable=False)

    week = data.get("week")
    if _keys("$.week", week, {"days"}, e):
        days = week.get("days")
        if not isinstance(days, list) or len(days) > 8:
            e.append("$.week.days: must be a list (<=8)")
        else:
            for i, d in enumerate(days):
                if _keys(f"$.week.days[{i}]", d, ENERGY_KEYS | {"d"}, e):
                    _str(f"$.week.days[{i}].d", d.get("d"), DATE, e)
                    for k in ENERGY_KEYS:
                        _num(f"$.week.days[{i}].{k}", d.get(k), 0, MAX_WH, e)
    return e
