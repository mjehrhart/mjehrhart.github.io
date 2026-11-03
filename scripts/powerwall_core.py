"""Shared logic: Tesla Fleet API calls + allowlist sanitizer.

Standard library only (urllib), so it runs in AWS Lambda (python3.12+) and
locally without installing anything. NOTHING here logs tokens, site IDs, or
raw API responses.

Output schema (see RESEARCH.md / verify_public_data.py): only numbers, a few
booleans, and date/time strings in fixed formats. No IDs, names, tz strings,
UTC offsets, or locations ever leave this module.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:  # Lambda images may lack system tzdata; bundle the `tzdata` package if so.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
DEFAULT_API_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
SCHEMA_VERSION = 1
UA = "home-energy-dashboard/1.0"


class TeslaError(Exception):
    """Raised with a short, secret-free message."""


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
def _request(method: str, url: str, *, headers=None, data=None, timeout=30):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        # Only the status code and Tesla's short error enum; never the URL
        # (it contains the site id) or the body.
        code = e.code
        err = ""
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        raise TeslaError(f"HTTP {code} {str(err)[:40]}".strip()) from None
    except urllib.error.URLError as e:
        raise TeslaError(f"network error: {type(e.reason).__name__}") from None


def refresh_access_token(client_id: str, refresh_token: str) -> tuple[str, str]:
    """Exchange the (single-use) refresh token. Returns (access, NEW refresh).

    Tesla rotates refresh tokens: the returned refresh token MUST be stored for
    the next run. The previous one stays valid for ~24h as a safety net.
    """
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
    ).encode()
    tok = _request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    access = tok.get("access_token")
    new_refresh = tok.get("refresh_token") or refresh_token
    if not access:
        raise TeslaError("token response had no access_token")
    return access, new_refresh


def _api_get(api_base: str, access: str, path: str, params: dict | None = None) -> dict:
    url = api_base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = _request(
        "GET",
        url,
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
    )
    return data.get("response") or {}


# --------------------------------------------------------------------------
# Number helpers
# --------------------------------------------------------------------------
def _num(v, nd=0):
    """Finite float/int or None. Rounds to nd decimals (0 -> int)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v):
        return None
    return int(round(v)) if nd == 0 else round(float(v), nd)


def _sum(d: dict, keys) -> float | None:
    vals = [d.get(k) for k in keys if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)]
    return float(sum(vals)) if vals else None


def _pick(d: dict, primary: str, fallback_keys) -> float | None:
    v = d.get(primary)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return _sum(d, fallback_keys)


# --------------------------------------------------------------------------
# Field mapping (Tesla -> generic, allowlisted)
# --------------------------------------------------------------------------
# live_status (watts / percent). Sign convention (Powerwall gateway, confirmed
# by fixture arithmetic solar+battery+grid == load):
#   battery_power > 0 discharging, < 0 charging
#   grid_power    > 0 importing,   < 0 exporting
def sanitize_live(raw: dict) -> dict:
    out = {
        "solar_w": _num(raw.get("solar_power")),
        "home_w": _num(raw.get("load_power")),
        "battery_w": _num(raw.get("battery_power")),
        "grid_w": _num(raw.get("grid_power")),
        "battery_pct": _num(raw.get("percentage_charged"), 1),
    }
    gs = raw.get("grid_status")
    out["grid_online"] = (gs == "Active") if isinstance(gs, str) else None
    return out


# calendar_history kind=energy time_series entries (Wh). Tesla omits a field
# instead of sending 0, so missing -> 0 when the entry has any reading.
ENERGY_FIELDS = {
    "solar_wh": ("total_solar_generation", ["solar_energy_exported"]),
    "home_wh": (
        "total_home_usage",
        [
            "consumer_energy_imported_from_grid",
            "consumer_energy_imported_from_solar",
            "consumer_energy_imported_from_battery",
            "consumer_energy_imported_from_generator",
        ],
    ),
    "grid_import_wh": ("grid_energy_imported", []),
    "grid_export_wh": (
        "total_grid_energy_exported",
        [
            "grid_energy_exported_from_solar",
            "grid_energy_exported_from_battery",
            "grid_energy_exported_from_generator",
        ],
    ),
    "battery_charge_wh": (
        "total_battery_charge",
        [
            "battery_energy_imported_from_grid",
            "battery_energy_imported_from_solar",
            "battery_energy_imported_from_generator",
        ],
    ),
    "battery_discharge_wh": ("total_battery_discharge", ["battery_energy_exported"]),
}


def _entry_values(entry: dict) -> dict:
    return {k: (_pick(entry, p, fb) or 0.0) for k, (p, fb) in ENERGY_FIELDS.items()}


def _parse_ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def aggregate(series, tz, key_fn) -> dict:
    """Group time_series entries by key_fn(local_dt) and sum the Wh fields."""
    buckets: dict[str, dict] = {}
    for entry in series or []:
        if not isinstance(entry, dict):
            continue
        dt = _parse_ts(entry.get("timestamp"))
        if dt is None:
            continue
        key = key_fn(dt.astimezone(tz))
        vals = _entry_values(entry)
        b = buckets.setdefault(key, dict.fromkeys(ENERGY_FIELDS, 0.0))
        for k, v in vals.items():
            b[k] += v
    return buckets


def _round_bucket(b: dict) -> dict:
    return {k: _num(v) for k, v in b.items()}


def get_tz(tz_name: str):
    if ZoneInfo is None:
        raise TeslaError("zoneinfo unavailable")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        raise TeslaError("invalid SITE_TZ (bundle the tzdata package?)") from None


# --------------------------------------------------------------------------
# Main fetch
# --------------------------------------------------------------------------
def fetch_snapshot(*, api_base: str, access: str, site_id: str, tz_name: str,
                   previous: dict | None = None, now: datetime | None = None) -> dict:
    tz = get_tz(tz_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_today = today + timedelta(days=1) - timedelta(seconds=1)
    week_start = today - timedelta(days=6)
    base = f"/api/1/energy_sites/{urllib.parse.quote(str(site_id))}"

    live_raw = _api_get(api_base, access, f"{base}/live_status")
    day_raw = _api_get(api_base, access, f"{base}/calendar_history", {
        "kind": "energy", "period": "day",
        "start_date": today.isoformat(), "end_date": end_today.isoformat(),
        "time_zone": tz_name,
    })
    week_raw = _api_get(api_base, access, f"{base}/calendar_history", {
        "kind": "energy", "period": "week",
        "start_date": week_start.isoformat(), "end_date": end_today.isoformat(),
        "time_zone": tz_name,
    })
    return build_public_data(live_raw, day_raw, week_raw, tz, now_local, previous)


def build_public_data(live_raw, day_raw, week_raw, tz, now_local, previous=None, example=False) -> dict:
    today_str = now_local.strftime("%Y-%m-%d")
    live = sanitize_live(live_raw or {})

    day_b = aggregate((day_raw or {}).get("time_series"), tz,
                      lambda d: d.strftime("%H:%M") if d.strftime("%Y-%m-%d") == today_str else None)
    day_b.pop(None, None)
    day_points = [{"t": t, **_round_bucket(v)} for t, v in sorted(day_b.items())]

    week_b = aggregate((week_raw or {}).get("time_series"), tz, lambda d: d.strftime("%Y-%m-%d"))
    oldest = (now_local - timedelta(days=6)).strftime("%Y-%m-%d")
    week_days = [{"d": d, **_round_bucket(v)} for d, v in sorted(week_b.items())
                 if oldest <= d <= today_str]

    day_totals = {k: _num(sum((p[k] or 0) for p in day_points)) for k in ENERGY_FIELDS}

    # Battery % trail: carry forward today's snapshots from the previous data.json
    trail = []
    if isinstance(previous, dict) and not previous.get("example"):
        pd = previous.get("day") or {}
        if pd.get("date") == today_str:
            for p in pd.get("battery_pct_trail") or []:
                if isinstance(p, dict) and isinstance(p.get("t"), str):
                    pct = _num(p.get("pct"), 1)
                    if pct is not None:
                        trail.append({"t": p["t"][:5], "pct": pct})
    if live["battery_pct"] is not None:
        t_now = now_local.strftime("%H:%M")
        trail = [p for p in trail if p["t"] != t_now] + [{"t": t_now, "pct": live["battery_pct"]}]
    trail = sorted(trail, key=lambda p: p["t"])[-200:]

    return {
        "schema": SCHEMA_VERSION,
        "example": bool(example),
        "generated_at": now_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live": live,
        "day": {"date": today_str, "points": day_points, "totals": day_totals,
                "battery_pct_trail": trail},
        "week": {"days": week_days},
    }
