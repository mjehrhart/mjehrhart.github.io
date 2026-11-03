/* Home Energy Dashboard: renders the sanitized public snapshot in data.json.
   No third-party requests, no cookies, no identifiers. */
"use strict";
const STALE_MS = 2 * 60 * 60 * 1000;
const C = { solar: "#f5b301", home: "#4aa3ff", batt: "#3ccf8e", imp: "#ff6b6b", exp: "#b48cff", grid: "#2a3542", text: "#8b98a5" };
const $ = (id) => document.getElementById(id);
const kw = (w) => (w == null ? "–" : (Math.abs(w) / 1000).toFixed(Math.abs(w) < 10000 ? 2 : 1) + " kW");
const kwh = (wh) => (wh == null ? "–" : (wh / 1000).toFixed(1) + " kWh");

function fmtDate(d) { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }); }

function kpis(d) {
  const l = d.live || {}, t = (d.day && d.day.totals) || {};
  $("k-solar").textContent = kw(l.solar_w);
  $("k-solar-sub").textContent = "Today " + kwh(t.solar_wh);
  $("k-batt").textContent = l.battery_pct == null ? "–" : Math.round(l.battery_pct) + "%";
  const bw = l.battery_w;
  $("k-batt-sub").textContent = bw == null ? "–" : Math.abs(bw) < 50 ? "Idle" : (bw > 0 ? "Discharging " : "Charging ") + kw(bw);
  $("k-home").textContent = kw(l.home_w);
  $("k-home-sub").textContent = "Today " + kwh(t.home_wh);
  const gw = l.grid_w;
  $("k-grid").textContent = gw == null ? "–" : Math.abs(gw) < 50 ? "0 kW" : (gw > 0 ? "↓ " : "↑ ") + kw(gw);
  $("k-grid-sub").textContent = (l.grid_online === false ? "Off-grid · " : gw > 50 ? "Importing · " : gw < -50 ? "Exporting · " : "") +
    "In " + kwh(t.grid_import_wh) + " / Out " + kwh(t.grid_export_wh);
}

function baseOpts(yTitle) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: C.text, boxWidth: 12 } } },
    scales: {
      x: { ticks: { color: C.text, maxRotation: 0, autoSkip: true, autoSkipPadding: 16, maxTicksLimit: 9 }, grid: { color: C.grid } },
      y: { ticks: { color: C.text }, grid: { color: C.grid }, title: { display: !!yTitle, text: yTitle, color: C.text } },
    },
  };
}

function minutes(t) { const [h, m] = t.split(":").map(Number); return h * 60 + m; }

function dayChart(d) {
  const pts = (d.day && d.day.points) || [];
  let step = 60;
  if (pts.length > 1) step = Math.max(5, minutes(pts[1].t) - minutes(pts[0].t)) || 60;
  const toKw = (wh) => (wh == null ? null : +(wh / 1000 / (step / 60)).toFixed(2));
  const ds = (label, key, color, sign = 1) => ({ label, data: pts.map((p) => (p[key] == null ? null : sign * toKw(p[key]))), borderColor: color, backgroundColor: color + "33", fill: false, pointRadius: 0, borderWidth: 2, tension: 0.3, spanGaps: true });
  const o = baseOpts("kW");
  o.plugins.tooltip = { callbacks: { label: (c) => `${c.dataset.label}: ${Math.abs(c.parsed.y).toFixed(2)} kW` } };
  new Chart($("c-day"), { type: "line", data: { labels: pts.map((p) => p.t), datasets: [
    ds("Solar", "solar_wh", C.solar), ds("Home", "home_wh", C.home),
    ds("Grid import", "grid_import_wh", C.imp), ds("Grid export", "grid_export_wh", C.exp, -1),
  ] }, options: o });
}

function battChart(d) {
  const tr = (d.day && d.day.battery_pct_trail) || [];
  const o = baseOpts("%"); o.scales.y.min = 0; o.scales.y.max = 100; o.plugins.legend.display = false;
  new Chart($("c-batt"), { type: "line", data: { labels: tr.map((p) => p.t), datasets: [{ label: "Charge", data: tr.map((p) => p.pct), borderColor: C.batt, backgroundColor: C.batt + "26", fill: true, pointRadius: 0, borderWidth: 2, tension: 0.3 }] }, options: o });
}

function weekChart(d) {
  const days = (d.week && d.week.days) || [];
  const ds = (label, key, color) => ({ label, data: days.map((x) => (x[key] == null ? null : +(x[key] / 1000).toFixed(1))), backgroundColor: color, borderRadius: 3 });
  new Chart($("c-week"), { type: "bar", data: { labels: days.map((x) => fmtDate(x.d)), datasets: [
    ds("Solar", "solar_wh", C.solar), ds("Home", "home_wh", C.home), ds("Grid import", "grid_import_wh", C.imp), ds("Grid export", "grid_export_wh", C.exp),
  ] }, options: baseOpts("kWh") });
}

async function main() {
  try {
    const r = await fetch("data.json?v=" + Math.floor(Date.now() / 60000), { cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    const ts = new Date(d.generated_at);
    $("updated").textContent = "Last updated " + ts.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    if (d.example) $("example").hidden = false;
    else if (Date.now() - ts.getTime() > STALE_MS) $("stale").hidden = false;
    kpis(d);
    if (window.Chart) { Chart.defaults.font.family = getComputedStyle(document.body).fontFamily; dayChart(d); battChart(d); weekChart(d); }
  } catch (e) {
    $("updated").textContent = "No data";
    $("error").textContent = "Could not load the latest snapshot. Please try again later.";
    $("error").hidden = false;
  }
}
main();
