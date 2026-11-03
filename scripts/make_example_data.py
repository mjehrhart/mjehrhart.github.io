#!/usr/bin/env python3
"""Generate clearly-labelled EXAMPLE data (synthetic, "example": true) so the
page renders before setup. Runs the synthetic Tesla-shaped responses through
the real sanitizer, so it also exercises the allowlist path."""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import powerwall_core as core  # noqa: E402
from public_schema import validate  # noqa: E402

tz = timezone.utc  # synthetic data: no real location/timezone
now = datetime.now(tz).replace(second=0, microsecond=0)
now = now.replace(hour=15, minute=30)  # fixed mid-afternoon for a representative example
today = now.replace(hour=0, minute=0)


def solar_w(h):  # bell curve peaking ~13:00, 6 kW system
    return max(0.0, 6000 * math.sin(math.pi * (h - 6.5) / 13)) if 6.5 < h < 19.5 else 0.0


def home_w(h):
    return 450 + 900 * math.exp(-((h - 7.5) ** 2) / 2) + 1800 * math.exp(-((h - 19) ** 2) / 4)


day_series, soc, trail = [], 30.0, []
for i in range(int((now - today).total_seconds() // 900)):  # 15-min intervals
    h = i / 4
    s, l = solar_w(h) / 4, home_w(h) / 4  # Wh per 15 min
    surplus = s - l
    charge = min(max(surplus, 0), 240) if soc < 100 else 0
    discharge = min(max(-surplus, 0), 1000) if soc > 20 else 0
    soc = min(100, max(0, soc + (charge - discharge) / 135))
    gi = max(-surplus - discharge, 0)
    ge = max(surplus - charge, 0)
    day_series.append({
        "timestamp": (today + timedelta(minutes=15 * i)).isoformat(),
        "solar_energy_exported": s, "grid_energy_imported": gi,
        "grid_energy_exported_from_solar": ge,
        "battery_energy_exported": discharge, "battery_energy_imported_from_solar": charge,
        "consumer_energy_imported_from_solar": min(s, l),
        "consumer_energy_imported_from_battery": discharge,
        "consumer_energy_imported_from_grid": gi,
    })
    if i % 2 == 0:
        trail.append({"t": (today + timedelta(minutes=15 * i)).strftime("%H:%M"), "pct": round(soc, 1)})

week_series = []
for d in range(6, -1, -1):
    day = today - timedelta(days=d)
    k = 1 - 0.12 * ((d * 37) % 5)  # some cloudy days
    sol = 38000 * k if d else sum(e["solar_energy_exported"] for e in day_series)
    home = 24000 + 2500 * ((d * 13) % 3) if d else sum(e["consumer_energy_imported_from_solar"] + e["consumer_energy_imported_from_battery"] + e["consumer_energy_imported_from_grid"] for e in day_series)
    imp = max(1500, home - sol * 0.55) if d else sum(e["grid_energy_imported"] for e in day_series)
    exp = max(0, sol - home * 0.45 - 9000) if d else sum(e["grid_energy_exported_from_solar"] for e in day_series)
    week_series.append({"timestamp": day.isoformat(), "total_solar_generation": sol, "total_home_usage": home,
                        "grid_energy_imported": imp, "total_grid_energy_exported": exp,
                        "total_battery_charge": 9500 * k if d else sum(e["battery_energy_imported_from_solar"] for e in day_series),
                        "total_battery_discharge": 9000 * k if d else sum(e["battery_energy_exported"] for e in day_series)})

live = {"solar_power": round(solar_w(15.5)), "load_power": round(home_w(15.5)), "percentage_charged": round(soc, 1),
        "battery_power": -960, "grid_power": round(home_w(15.5) + 960 - solar_w(15.5)), "grid_status": "Active"}
prev = {"example": False, "day": {"date": today.strftime("%Y-%m-%d"), "battery_pct_trail": trail}}
data = core.build_public_data(live, {"time_series": day_series}, {"time_series": week_series}, tz, now, prev, example=True)
errs = validate(data)
assert not errs, errs
(ROOT / "data.json").write_text(json.dumps(data, indent=1))
print("wrote data.json (EXAMPLE data)")
