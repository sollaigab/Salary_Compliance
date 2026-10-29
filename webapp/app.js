// Salary transparency observatory: reads the aggregate cells (data/aggregates.json).
// Each cell = a filter combination (period, sector, function, work arrangement; 'all' = no filter)
// x one dimension. Cells below the thresholds are not in the file: the charts show only what the
// protection rules made public.

const nf = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1, useGrouping: "always" });
const nf0 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0, useGrouping: "always" });
const nfPct = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const df = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

const $ = (sel) => document.querySelector(sel);
let cells = [], labels = {};

// filter -> the dimension it fixes: when a filter is set, the chart of its own dimension shows
// every value (without that filter) and highlights the selected one
const FILTERS = { country: "country", macro: "macro_sector", function: "job_function", workplace: "workplace_type" };
// Italy by default: the country where the obligation is in force; the others are the comparison
const DEFAULTS = { period: "post_law", country: "IT", macro: "all", function: "all", workplace: "all" };
const COUNTRIES = ["IT", "DE", "NL", "FR"];
const WORKPLACES = ["onsite", "hybrid", "remote"];

// ------------------------------------------------------------------ theme

function setupTheme() {
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem("theme");
    if (saved) root.dataset.theme = saved;
  } catch { /* storage unavailable: follow the system */ }
  $("#theme-toggle").addEventListener("click", () => {
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch { /* ignore */ }
  });
}

// ------------------------------------------------------------------ data

const label = (dim, value) =>
  (labels[dim] && labels[dim][value]) || (value === "not_stated" ? "Not stated" : value);
const pct = (v) => (v == null ? "-" : `${nfPct.format(v)}%`);
const euro = (v) => (v == null ? "-" : `€${nf0.format(v)}`);
const range = (lo, hi) => (lo == null ? "-" : lo === hi ? euro(lo) : `${euro(lo)} - ${euro(hi)}`);

// cells of one filter combination and one dimension
function select(f, dim) {
  return cells.filter((c) => c.dim === dim && c.period === f.period && c.country === f.country
    && c.macro === f.macro && c.function === f.function && c.workplace === f.workplace);
}

// cells for the chart of a dimension: if a filter fixes that dimension, drop the filter and
// highlight the selected value instead
function forDim(f, dim) {
  const axis = Object.keys(FILTERS).find((k) => FILTERS[k] === dim);
  if (axis && f[axis] !== "all") {
    return { rows: named(select({ ...f, [axis]: "all" }, dim), dim), highlight: f[axis] };
  }
  return { rows: named(select(f, dim), dim), highlight: null };
}

function readFilters() {
  const form = new FormData($("#filters"));
  return Object.fromEntries(Object.entries(DEFAULTS).map(([k, d]) => [k, form.get(k) || d]));
}

function filtersToHash(f) {
  const params = new URLSearchParams();
  for (const [k, d] of Object.entries(DEFAULTS)) if (f[k] !== d) params.set(k, f[k]);
  const hash = params.toString();
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname);
}

function filtersFromHash() {
  for (const [k, v] of new URLSearchParams(location.hash.slice(1))) {
    const el = $(`#filters [name="${k}"]`);
    if (el && [...el.options].some((o) => o.value === v)) el.value = v;
  }
}

// ------------------------------------------------------------------ tooltip

const tip = $("#tooltip");
addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
function attachTooltip(el, value, name, extra = "") {
  const show = (x, y) => {
    tip.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = value;
    const span = document.createElement("span");
    span.textContent = extra ? `${name} · ${extra}` : name;
    tip.append(strong, span);
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = `${Math.min(x + 14, innerWidth - w - 8)}px`;
    tip.style.top = `${Math.max(8, y - h - 12)}px`;
  };
  // touch has no hover and no pointer leaving the element: no tooltip there (the table view has the numbers)
  el.addEventListener("pointermove", (e) => { if (e.pointerType !== "touch") show(e.clientX, e.clientY); });
  // keyboard focus only: a tap also focuses the row, and the tooltip would stay open over the page
  el.addEventListener("focus", () => {
    if (!el.matches(":focus-visible")) return;
    const r = el.getBoundingClientRect();
    show(r.left + r.width / 2, r.top);
  });
  el.addEventListener("pointerleave", () => { tip.hidden = true; });
  el.addEventListener("blur", () => { tip.hidden = true; });
}

// ------------------------------------------------------------------ chart parts

function axis(ticks, toPct, fmt, extraClass = "") {
  const row = document.createElement("div");
  row.className = `axis ${extraClass}`;
  row.append(document.createElement("span"));
  const scale = document.createElement("div");
  scale.className = "axis-scale";
  for (const t of ticks) {
    const s = document.createElement("span");
    s.style.left = `${toPct(t)}%`;
    s.textContent = fmt(t);
    scale.append(s);
  }
  row.append(scale);
  return row;
}

function gridlines(ticks, toPct) {
  const g = document.createElement("div");
  g.className = "gridlines";
  for (const t of ticks) {
    const i = document.createElement("i");
    i.style.left = `${toPct(t)}%`;
    g.append(i);
  }
  return g;
}

// one chart row: label column + plot area (the same grid in every chart, so they all line up)
function chartRow(name, plotClass) {
  const row = document.createElement("div");
  row.className = "bar-row";
  row.tabIndex = 0;
  const lab = document.createElement("span");
  lab.className = "bar-label";
  lab.textContent = name;
  const plot = document.createElement("div");
  plot.className = plotClass;
  row.append(lab, plot);
  return [row, plot];
}

const NO_DATA = "With these filters there are too few job ads or companies: the detail would make single companies recognizable.";

function emptyChart(fig) {
  const p = document.createElement("p");
  p.className = "chart-empty";
  p.textContent = NO_DATA;
  fig.querySelector(".chart-body").replaceChildren(p);
  renderTableView(fig, [], []);
}

const PCT_TICKS = [0, 25, 50, 75, 100];

// horizontal bars, one series; with "highlight" the other bars turn grey (emphasis)
function renderBars(fig, rows, { highlight = null } = {}) {
  if (!rows.length) return emptyChart(fig);
  const toPct = (v) => v;
  const bars = document.createElement("div");
  bars.className = "bars";
  for (const r of rows) {
    const [row, plot] = chartRow(r.name, "bar-plot");
    if (highlight && r.value === highlight) row.classList.add("is-selected");
    plot.append(gridlines(PCT_TICKS, toPct));
    const bar = document.createElement("div");
    bar.className = highlight && r.value !== highlight ? "bar bar-muted" : "bar";
    bar.style.width = `${Math.max(0, Math.min(100, r.pct_with_salary))}%`;
    const val = document.createElement("span");
    val.className = "bar-value";
    val.textContent = pct(r.pct_with_salary);
    plot.append(bar, val);
    attachTooltip(row, pct(r.pct_with_salary), r.name, `${nf0.format(r.n_jobs)} job ads, ${r.n_companies} companies`);
    bars.append(row);
  }
  fig.querySelector(".chart-body").replaceChildren(bars, axis(PCT_TICKS, toPct, (v) => `${v}%`));
  renderTableView(fig, ["", "Job ads", "Companies", "State the pay", "Vague wording only"],
    rows.map((r) => [r.name, nf0.format(r.n_jobs), nf0.format(r.n_companies), pct(r.pct_with_salary), pct(r.pct_vague)]));
}

// salary domain rounded to €10k, shared by the salary charts so their axes line up
function salaryDomain(rows) {
  rows = rows.filter((r) => r.ral_min_median != null);
  if (!rows.length) return null;
  return [Math.floor(Math.min(...rows.map((r) => r.ral_min_median)) / 10000) * 10000,
          Math.ceil(Math.max(...rows.map((r) => r.ral_max_median ?? r.ral_min_median)) / 10000) * 10000];
}

// dumbbell: minimum and maximum of the range (medians), two shades of the same blue
function renderDumbbell(fig, rows, domain, { highlight = null } = {}) {
  rows = rows.filter((r) => r.ral_min_median != null);
  if (!rows.length || !domain) return emptyChart(fig);
  const [lo, hi] = domain;
  const step = (hi - lo) / 10000 > 6 ? 20000 : 10000;
  const ticks = [];
  for (let t = lo; t <= hi; t += step) ticks.push(t);
  const toPct = (v) => ((v - lo) / (hi - lo || 1)) * 100;
  const bars = document.createElement("div");
  bars.className = "bars";
  for (const r of rows) {
    const top = r.ral_max_median ?? r.ral_min_median;
    const [row, plot] = chartRow(r.name, "dumb-plot");
    if (highlight) row.classList.add(r.value === highlight ? "is-selected" : "is-muted");
    plot.append(gridlines(ticks, toPct));
    const line = document.createElement("div");
    line.className = "dumb-line";
    line.style.left = `${toPct(r.ral_min_median)}%`;
    line.style.width = `${toPct(top) - toPct(r.ral_min_median)}%`;
    const dLo = document.createElement("div");
    dLo.className = "dot dot-lo";
    dLo.style.left = `${toPct(r.ral_min_median)}%`;
    const dHi = document.createElement("div");
    dHi.className = "dot dot-hi";
    dHi.style.left = `${toPct(top)}%`;
    plot.append(line, dLo, dHi);
    attachTooltip(row, range(r.ral_min_median, top), r.name, `${nf0.format(r.n_ral)} job ads with a figure`);
    bars.append(row);
  }
  fig.querySelector(".chart-body").replaceChildren(bars, axis(ticks, toPct, (v) => `€${nf0.format(v / 1000)}k`));
  renderTableView(fig, ["", "Job ads with a figure", "Minimum (median)", "Maximum (median)"],
    rows.map((r) => [r.name, nf0.format(r.n_ral), euro(r.ral_min_median), euro(r.ral_max_median)]));
}

// 100% stacked bars: work arrangement mix per sector. Segments hidden by the protection rules
// are not guessed: what is missing from 100% is drawn as a striped "hidden" part.
const MIX_PARTS = [...WORKPLACES, "not_stated"];
function renderMix(fig, rows, { highlight = null } = {}) {
  if (!rows.length) return emptyChart(fig);
  const toPct = (v) => v;
  const bars = document.createElement("div");
  bars.className = "bars";
  for (const r of rows) {
    const [row, plot] = chartRow(r.name, "bar-plot mix-plot");
    if (r.isTotal) row.classList.add("is-total");
    plot.append(gridlines(PCT_TICKS, toPct));
    const stack = document.createElement("div");
    stack.className = "stack";
    let shown = 0;
    const parts = [];
    for (const key of MIX_PARTS) {
      const share = r.parts[key];
      if (share == null) continue;
      shown += share;
      parts.push([key, share]);
    }
    if (100 - shown >= 0.5) parts.push(["hidden", 100 - shown]);
    for (const [key, share] of parts) {
      const seg = document.createElement("span");
      seg.className = `seg seg-${key}`;
      if (highlight && key !== highlight) seg.classList.add("is-muted");
      seg.style.width = `${share}%`;
      stack.append(seg);
    }
    plot.append(stack);
    const summary = parts.map(([k, s]) => `${k === "hidden" ? "Hidden" : label("workplace_type", k)} ${nf0.format(s)}%`).join(", ");
    attachTooltip(row, `${nf0.format(r.n_jobs)} job ads`, r.name, summary);
    bars.append(row);
  }
  fig.querySelector(".chart-body").replaceChildren(bars, axis(PCT_TICKS, toPct, (v) => `${v}%`));
  renderTableView(fig, ["", "Job ads", ...MIX_PARTS.map((k) => label("workplace_type", k))],
    rows.map((r) => [r.name, nf0.format(r.n_jobs), ...MIX_PARTS.map((k) => (r.parts[k] == null ? "hidden" : pct(r.parts[k])))]));
}

function renderTableView(fig, head, rows) {
  const holder = fig.querySelector(".table-view div");
  const table = document.createElement("table");
  table.className = "data";
  const thead = table.createTHead().insertRow();
  head.forEach((h, i) => { const th = document.createElement("th"); th.textContent = h; th.scope = "col"; if (i) th.className = "num"; thead.append(th); });
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    r.forEach((c, i) => { const td = tr.insertCell(); td.textContent = c; if (i) td.className = "num"; });
  }
  holder.replaceChildren(table);
}

// ------------------------------------------------------------------ update

const named = (rows, dim) => rows.map((r) => ({ ...r, name: label(dim, r.value) }));
const byPct = (a, b) => b.pct_with_salary - a.pct_with_salary || b.n_jobs - a.n_jobs;
const byTop = (a, b) => (b.ral_max_median ?? b.ral_min_median ?? -1) - (a.ral_max_median ?? a.ral_min_median ?? -1);
const inOrder = (order) => (a, b) => order.indexOf(a.value) - order.indexOf(b.value);
const SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead", "manager", "director", "executive"];
const PERIOD_ORDER = ["old", "pre_law", "post_law"];
const WORKPLACE_ORDER = [...WORKPLACES, "not_stated"];
const stated = (r) => r.value !== "not_stated";

// work arrangement mix for one filter combination: shares of the total, null if hidden
function mixRow(f, name, isTotal = false) {
  const [total] = select({ ...f, workplace: "all" }, "total");
  if (!total) return null;
  const parts = {};
  for (const c of select({ ...f, workplace: "all" }, "workplace_type")) parts[c.value] = (100 * c.n_jobs) / total.n_jobs;
  return { name, isTotal, n_jobs: total.n_jobs, parts };
}

function update() {
  const results = $("#results");
  results.classList.add("is-loading");
  refreshOptions();
  const f = readFilters();
  filtersToHash(f);

  const [k] = select(f, "total");
  $("#kpi-pct").textContent = k ? pct(k.pct_with_salary) : "-";
  $("#kpi-ral").textContent = k ? range(k.ral_min_median, k.ral_max_median) : "-";
  $("#kpi-jobs").textContent = k ? nf0.format(k.n_jobs) : "-";
  $("#kpi-companies").textContent = k ? nf0.format(k.n_companies) : "-";
  $("#kpi-vague").textContent = k ? pct(k.pct_vague) : "-";
  $("#kpi-empty").hidden = Boolean(k);

  // who states the pay
  const country = forDim(f, "country");
  renderBars($("#chart-country"), country.rows.sort(inOrder([...COUNTRIES, "not_stated"])), { highlight: country.highlight });
  const macro = forDim(f, "macro_sector");
  renderBars($("#chart-macro"), macro.rows.sort(byPct), { highlight: macro.highlight });
  renderBars($("#chart-region"), forDim(f, "region").rows.sort(byPct));
  renderBars($("#chart-contract"), forDim(f, "contract_type").rows.filter(stated).sort(byPct));
  const wp = forDim(f, "workplace_type");
  renderBars($("#chart-workplace-pct"), wp.rows.sort(inOrder(WORKPLACE_ORDER)), { highlight: wp.highlight });
  renderBars($("#chart-period"),
    named(select({ ...f, period: "all" }, "posting_period"), "posting_period").filter(stated).sort(inOrder(PERIOD_ORDER)));

  // how much they offer: one salary scale for the four charts
  const fn = forDim(f, "job_function");
  const byFunction = fn.rows.sort(byTop);
  const bySeniority = forDim(f, "seniority").rows.filter(stated).sort(inOrder(SENIORITY_ORDER));
  const byRegion = forDim(f, "region").rows.sort(byTop);
  const byWorkplace = wp.rows.sort(inOrder(WORKPLACE_ORDER));
  const scale = salaryDomain([...byFunction, ...bySeniority, ...byRegion, ...byWorkplace]);
  renderDumbbell($("#chart-function"), byFunction, scale, { highlight: fn.highlight });
  renderDumbbell($("#chart-seniority"), bySeniority, scale);
  renderDumbbell($("#chart-region-pay"), byRegion, scale);
  renderDumbbell($("#chart-workplace-pay"), byWorkplace, scale, { highlight: wp.highlight });

  // where the work happens: all sectors first, then each sector (the sector filter highlights one)
  const mix = [mixRow({ ...f, macro: "all" }, "All sectors", true)];
  const sectors = Object.keys(labels.macro_sector)
    .map((code) => mixRow({ ...f, macro: code }, label("macro_sector", code)))
    .filter(Boolean)
    .sort((a, b) => (b.parts.onsite ?? 0) - (a.parts.onsite ?? 0));
  renderMix($("#chart-workplace-mix"), [...mix.filter(Boolean), ...sectors], { highlight: f.workplace !== "all" ? f.workplace : null });

  results.classList.remove("is-loading");
}

async function loadAll() {
  const [meta, lab, data] = await Promise.all(
    ["data/meta.json", "data/labels.json", "data/aggregates.json"].map((u) => fetch(u).then((r) => {
      if (!r.ok) throw new Error(`${u}: ${r.status}`);
      return r.json();
    })));
  labels = lab;
  cells = data;
  meta.rules.max_share_pct = `${Math.round(meta.rules.max_share * 100)}%`;
  document.querySelectorAll("[data-meta]").forEach((el) => {
    const v = el.dataset.meta.split(".").reduce((o, k) => o && o[k], meta);
    el.textContent = /^\d{4}-\d{2}-\d{2}$/.test(v) ? df.format(new Date(v)) : typeof v === "number" ? nf0.format(v) : v;
  });
  $("#stamp").textContent = `Job ads observed since ${df.format(new Date(meta.observed_from))}. Last update: ${df.format(new Date(meta.last_update))}.`;

  // every possible option of each filter, in display order ("all" first); refreshOptions() then
  // keeps only the ones usable with the other filters
  const options = (dim, codes, sorted = true) => {
    const list = codes.map((code) => [code, label(dim, code)]);
    if (sorted) list.sort((a, b) => a[1].localeCompare(b[1]));
    return list;
  };
  for (const el of document.querySelectorAll("#filters select")) {
    allOptions[el.name] = [...el.options].map((o) => [o.value, o.text]);
  }
  allOptions.country.push(...options("country", COUNTRIES, false));
  allOptions.macro.push(...options("macro_sector", Object.keys(labels.macro_sector)));
  allOptions.function.push(...options("job_function", Object.keys(labels.job_function)));
  allOptions.workplace.push(...options("workplace_type", WORKPLACES, false));
  // start with every option, so that a link can select any value; update() narrows them down
  for (const [name, list] of Object.entries(allOptions)) {
    $(`#filters [name="${name}"]`).replaceChildren(
      ...list.map(([code, text]) => new Option(text, code, code === DEFAULTS[name], code === DEFAULTS[name])));
  }
}

// each filter lists only the values that, with the other filters as they are, have published
// figures: any choice leads to data. The current value is always kept (it may come from a link).
const allOptions = {};
function refreshOptions() {
  const f = readFilters();
  for (const [name, list] of Object.entries(allOptions)) {
    const el = $(`#filters [name="${name}"]`);
    const usable = list.filter(([code]) => code === f[name]
      || select({ ...f, [name]: code }, "total").length > 0);
    // defaultSelected = the default value, so that "Reset filters" goes back to it
    el.replaceChildren(...usable.map(([code, text]) => new Option(text, code, code === DEFAULTS[name], code === f[name])));
  }
}

async function main() {
  setupTheme();
  try {
    await loadAll();
    filtersFromHash();
  } catch (err) {
    console.error(err);
    const e = $("#error");
    e.hidden = false;
    e.textContent = location.protocol === "file:"
      ? "Open the page through a local server, not as a file. From the project folder: python -m http.server -d webapp 8000, then open http://localhost:8000"
      : "The data could not be loaded. Reload the page; if the problem persists, check your connection.";
    $("#results").hidden = true;
    $("#stamp").textContent = "";
    return;
  }
  const form = $("#filters");
  form.addEventListener("change", update);
  form.addEventListener("reset", () => setTimeout(update, 0));
  form.addEventListener("submit", (e) => e.preventDefault());
  update();
}

main();
