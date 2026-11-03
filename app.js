/* Home Energy Dashboard: renders the sanitized public snapshot in data.json.
   Re-reads that file while the tab is visible. No third-party requests, no cookies, no identifiers.
   Day or night follows the viewer's local clock only (light 06:30-18:30). ?theme=light or ?theme=dark overrides.
   The matching one-line script in index.html sets data-theme before first paint. */
"use strict";
const STALE_MS = 2 * 60 * 60 * 1000;
const REFRESH_MS = 5 * 60 * 1000;
const THEME_TICK_MS = 60 * 1000;
const LIGHT_START_MIN = 6 * 60 + 30;
const LIGHT_END_MIN = 18 * 60 + 30;
const FALLBACK_COLORS = {
  solar: "#f5b301", home: "#4aa3ff", batt: "#3ccf8e", imp: "#ff6b6b", exp: "#b48cff",
  grid: "#2a3542", text: "#8b98a5",
  tooltipBg: "rgba(0, 0, 0, 0.8)", tooltipText: "#fff", tooltipBorder: "transparent", tooltipBorderWidth: 0,
};
let C = Object.assign({}, FALLBACK_COLORS);

function syncThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => {
    const value = style.getPropertyValue(name).trim();
    return value || fallback;
  };
  const width = Number(pick("--tooltip-border-width", String(FALLBACK_COLORS.tooltipBorderWidth)));
  C = {
    solar: pick("--solar", FALLBACK_COLORS.solar),
    home: pick("--home", FALLBACK_COLORS.home),
    batt: pick("--batt", FALLBACK_COLORS.batt),
    imp: pick("--imp", FALLBACK_COLORS.imp),
    exp: pick("--exp", FALLBACK_COLORS.exp),
    grid: pick("--chart-grid", FALLBACK_COLORS.grid),
    text: pick("--chart-text", FALLBACK_COLORS.text),
    tooltipBg: pick("--tooltip-bg", FALLBACK_COLORS.tooltipBg),
    tooltipText: pick("--tooltip-text", FALLBACK_COLORS.tooltipText),
    tooltipBorder: pick("--tooltip-border", FALLBACK_COLORS.tooltipBorder),
    tooltipBorderWidth: Number.isFinite(width) ? width : 0,
  };
}

function preferredTheme(now) {
  try {
    const q = new URLSearchParams(location.search).get("theme");
    if (q === "light" || q === "dark") return q;
  } catch (e) { /* clock decides when the query string cannot be read */ }
  const date = now || new Date();
  const mins = date.getHours() * 60 + date.getMinutes();
  return mins >= LIGHT_START_MIN && mins < LIGHT_END_MIN ? "light" : "dark";
}

function applyTheme() {
  const next = preferredTheme();
  const root = document.documentElement;
  if (root.getAttribute("data-theme") === next) return;
  root.setAttribute("data-theme", next);
  document.dispatchEvent(new CustomEvent("themechange"));
}
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

function tooltipOptions() {
  return {
    backgroundColor: C.tooltipBg,
    titleColor: C.tooltipText,
    bodyColor: C.tooltipText,
    footerColor: C.tooltipText,
    borderColor: C.tooltipBorder,
    borderWidth: C.tooltipBorderWidth,
  };
}

function baseOpts(yTitle) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: C.text, boxWidth: 12 } }, tooltip: tooltipOptions() },
    scales: {
      x: { ticks: { color: C.text, maxRotation: 0, autoSkip: true, autoSkipPadding: 16, maxTicksLimit: 9 }, grid: { color: C.grid } },
      y: { ticks: { color: C.text }, grid: { color: C.grid }, title: { display: !!yTitle, text: yTitle, color: C.text } },
    },
  };
}

const SERIES_COLOR = { Solar: "solar", Home: "home", "Grid import": "imp", "Grid export": "exp", Charge: "batt" };

function paintChart(chart) {
  syncThemeColors();
  chart.data.datasets.forEach((ds) => {
    const key = SERIES_COLOR[ds.label];
    if (!key) return;
    const color = C[key];
    if (chart.config.type === "bar") ds.backgroundColor = color;
    else {
      ds.borderColor = color;
      ds.backgroundColor = color + (ds.label === "Charge" ? "26" : "33");
    }
  });
  const opts = chart.options;
  if (opts.plugins && opts.plugins.legend && opts.plugins.legend.labels) opts.plugins.legend.labels.color = C.text;
  opts.plugins.tooltip = Object.assign({}, opts.plugins.tooltip, tooltipOptions());
  for (const id of ["x", "y"]) {
    const scale = opts.scales && opts.scales[id];
    if (!scale) continue;
    if (scale.ticks) scale.ticks.color = C.text;
    if (scale.grid) scale.grid.color = C.grid;
    if (scale.title) scale.title.color = C.text;
  }
  chart.update("none");
}

function restyleCharts() {
  if (!window.Chart) return;
  for (const id of ["c-day", "c-batt", "c-week"]) {
    const chart = Chart.getChart($(id));
    if (chart) paintChart(chart);
  }
}

function minutes(t) { const [h, m] = t.split(":").map(Number); return h * 60 + m; }

function mountChart(id, config) {
  const canvas = $(id);
  const prev = Chart.getChart(canvas);
  if (prev && prev.config.type === config.type) {
    prev.data.labels = config.data.labels;
    prev.data.datasets = config.data.datasets;
    paintChart(prev);
    return prev;
  }
  if (prev) prev.destroy();
  return new Chart(canvas, config);
}

function dayChart(d) {
  const pts = (d.day && d.day.points) || [];
  let step = 60;
  if (pts.length > 1) step = Math.max(5, minutes(pts[1].t) - minutes(pts[0].t)) || 60;
  const toKw = (wh) => (wh == null ? null : +(wh / 1000 / (step / 60)).toFixed(2));
  const ds = (label, key, color, sign = 1) => ({ label, data: pts.map((p) => (p[key] == null ? null : sign * toKw(p[key]))), borderColor: color, backgroundColor: color + "33", fill: false, pointRadius: 0, borderWidth: 2, tension: 0.3, spanGaps: true });
  const o = baseOpts("kW");
  o.plugins.tooltip.callbacks = { label: (c) => `${c.dataset.label}: ${Math.abs(c.parsed.y).toFixed(2)} kW` };
  mountChart("c-day", { type: "line", data: { labels: pts.map((p) => p.t), datasets: [
    ds("Solar", "solar_wh", C.solar), ds("Home", "home_wh", C.home),
    ds("Grid import", "grid_import_wh", C.imp), ds("Grid export", "grid_export_wh", C.exp, -1),
  ] }, options: o });
}

function battChart(d) {
  const tr = (d.day && d.day.battery_pct_trail) || [];
  const o = baseOpts("%"); o.scales.y.min = 0; o.scales.y.max = 100; o.plugins.legend.display = false;
  mountChart("c-batt", { type: "line", data: { labels: tr.map((p) => p.t), datasets: [{ label: "Charge", data: tr.map((p) => p.pct), borderColor: C.batt, backgroundColor: C.batt + "26", fill: true, pointRadius: 0, borderWidth: 2, tension: 0.3 }] }, options: o });
}

function weekChart(d) {
  const days = (d.week && d.week.days) || [];
  const ds = (label, key, color) => ({ label, data: days.map((x) => (x[key] == null ? null : +(x[key] / 1000).toFixed(1))), backgroundColor: color, borderRadius: 3 });
  mountChart("c-week", { type: "bar", data: { labels: days.map((x) => fmtDate(x.d)), datasets: [
    ds("Solar", "solar_wh", C.solar), ds("Home", "home_wh", C.home), ds("Grid import", "grid_import_wh", C.imp), ds("Grid export", "grid_export_wh", C.exp),
  ] }, options: baseOpts("kWh") });
}

let hasSnapshot = false;
let loading = false;

function showSnapshot(d) {
  const ts = new Date(d.generated_at);
  $("updated").textContent = "Last updated " + ts.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  $("example").hidden = !d.example;
  const age = Date.now() - ts.getTime();
  $("stale").hidden = !!d.example || !(age > STALE_MS);
  $("error").hidden = true;
  kpis(d);
  if (window.Chart) {
    syncThemeColors();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    dayChart(d);
    battChart(d);
    weekChart(d);
  }
  hasSnapshot = true;
}

async function loadSnapshot() {
  if (document.hidden || loading) return;
  loading = true;
  try {
    const r = await fetch("data.json?v=" + Math.floor(Date.now() / 60000), { cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    showSnapshot(await r.json());
  } catch (e) {
    if (hasSnapshot) return;
    $("updated").textContent = "No data";
    $("error").textContent = "Could not load the latest snapshot. Please try again later.";
    $("error").hidden = false;
  } finally {
    loading = false;
  }
}

document.addEventListener("themechange", () => { if (hasSnapshot) restyleCharts(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  applyTheme();
  loadSnapshot();
});
setInterval(applyTheme, THEME_TICK_MS);
setInterval(loadSnapshot, REFRESH_MS);
applyTheme();
loadSnapshot();
