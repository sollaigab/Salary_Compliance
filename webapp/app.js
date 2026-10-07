// Salary transparency observatory: reads the aggregate cells (data/aggregates.json).
// Each cell = period x sector x one dimension. Cells below the thresholds are not in the file:
// the charts show only what the protection rules made public.

const nf = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1, useGrouping: "always" });
const nf0 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0, useGrouping: "always" });
const df = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

const $ = (sel) => document.querySelector(sel);
let cells = [], labels = {};

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

const label = (dim, value) => (labels[dim] && labels[dim][value]) || value;
const pct = (v) => (v == null ? "-" : `${nf.format(v)}%`);
const euro = (v) => (v == null ? "-" : `€${nf0.format(v)}`);
const range = (lo, hi) => (lo == null ? "-" : lo === hi ? euro(lo) : `${euro(lo)} - ${euro(hi)}`);

function select(period, macro, dim) {
  return cells.filter((c) => c.period === period && c.macro === macro && c.dim === dim);
}

function readFilters() {
  const form = new FormData($("#filters"));
  return { period: form.get("period") || "post_law", macro: form.get("macro") || "all" };
}

function filtersToHash(f) {
  const params = new URLSearchParams();
  if (f.period !== "post_law") params.set("period", f.period);
  if (f.macro !== "all") params.set("macro", f.macro);
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

const NO_DATA = "With these filters there are too few job ads or companies: the detail would make single companies recognizable.";

function emptyChart(fig) {
  const p = document.createElement("p");
  p.className = "chart-empty";
  p.textContent = NO_DATA;
  fig.querySelector(".chart-body").replaceChildren(p);
  renderTableView(fig, [], []);
}

// horizontal bars, one series; with "highlight" the other bars turn grey (emphasis)
function renderBars(fig, rows, { highlight = null } = {}) {
  if (!rows.length) return emptyChart(fig);
  const body = fig.querySelector(".chart-body");
  const ticks = [0, 25, 50, 75, 100];
  const toPct = (v) => v;
  const bars = document.createElement("div");
  bars.className = "bars";
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.tabIndex = 0;
    const lab = document.createElement("span");
    lab.className = "bar-label";
    lab.textContent = r.name;
    const plot = document.createElement("div");
    plot.className = "bar-plot";
    plot.append(gridlines(ticks, toPct));
    const bar = document.createElement("div");
    bar.className = highlight && r.value !== highlight ? "bar bar-muted" : "bar";
    bar.style.width = `${Math.max(0, Math.min(100, r.pct_with_salary))}%`;
    const val = document.createElement("span");
    val.className = "bar-value";
    val.textContent = pct(r.pct_with_salary);
    plot.append(bar, val);
    row.append(lab, plot);
    attachTooltip(row, pct(r.pct_with_salary), r.name, `${nf0.format(r.n_jobs)} job ads, ${r.n_companies} companies`);
    bars.append(row);
  }
  body.replaceChildren(bars, axis(ticks, toPct, (v) => `${v}%`, "axis-bars"));
  renderTableView(fig, ["", "Job ads", "Companies", "State the pay", "Vague wording only"],
    rows.map((r) => [r.name, nf0.format(r.n_jobs), nf0.format(r.n_companies), pct(r.pct_with_salary), pct(r.pct_vague)]));
}

// dumbbell: minimum and maximum of the range (medians), two shades of the same blue
// salary domain rounded to €10k, shared by the salary charts so their axes line up
function salaryDomain(rows) {
  rows = rows.filter((r) => r.ral_min_median != null);
  if (!rows.length) return null;
  return [Math.floor(Math.min(...rows.map((r) => r.ral_min_median)) / 10000) * 10000,
          Math.ceil(Math.max(...rows.map((r) => r.ral_max_median)) / 10000) * 10000];
}

function renderDumbbell(fig, rows, domain = salaryDomain(rows)) {
  rows = rows.filter((r) => r.ral_min_median != null);
  if (!rows.length) return emptyChart(fig);
  const body = fig.querySelector(".chart-body");
  const [lo, hi] = domain;
  const step = (hi - lo) / 10000 > 6 ? 20000 : 10000;
  const ticks = [];
  for (let t = lo; t <= hi; t += step) ticks.push(t);
  const toPct = (v) => ((v - lo) / (hi - lo || 1)) * 100;
  const bars = document.createElement("div");
  bars.className = "bars";
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.tabIndex = 0;
    const lab = document.createElement("span");
    lab.className = "bar-label";
    lab.textContent = r.name;
    const plot = document.createElement("div");
    plot.className = "dumb-plot";
    plot.append(gridlines(ticks, toPct));
    const line = document.createElement("div");
    line.className = "dumb-line";
    line.style.left = `${toPct(r.ral_min_median)}%`;
    line.style.width = `${toPct(r.ral_max_median) - toPct(r.ral_min_median)}%`;
    const dLo = document.createElement("div");
    dLo.className = "dot dot-lo";
    dLo.style.left = `${toPct(r.ral_min_median)}%`;
    const dHi = document.createElement("div");
    dHi.className = "dot dot-hi";
    dHi.style.left = `${toPct(r.ral_max_median)}%`;
    plot.append(line, dLo, dHi);
    row.append(lab, plot);
    attachTooltip(row, range(r.ral_min_median, r.ral_max_median), r.name, `${nf0.format(r.n_ral)} job ads with a figure`);
    bars.append(row);
  }
  body.replaceChildren(bars, axis(ticks, toPct, (v) => `€${nf0.format(v / 1000)}k`));
  renderTableView(fig, ["", "Job ads with a figure", "Minimum (median)", "Maximum (median)"],
    rows.map((r) => [r.name, nf0.format(r.n_ral), euro(r.ral_min_median), euro(r.ral_max_median)]));
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
const SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead", "manager", "director", "executive"];
const PERIOD_ORDER = ["old", "pre_law", "post_law"];

function update() {
  const results = $("#results");
  results.classList.add("is-loading");
  const f = readFilters();
  filtersToHash(f);

  const [k] = select(f.period, f.macro, "total");
  $("#kpi-pct").textContent = k ? pct(k.pct_with_salary) : "-";
  $("#kpi-ral").textContent = k ? range(k.ral_min_median, k.ral_max_median) : "-";
  $("#kpi-jobs").textContent = k ? nf0.format(k.n_jobs) : "-";
  $("#kpi-companies").textContent = k ? nf0.format(k.n_companies) : "-";
  $("#kpi-vague").textContent = k ? pct(k.pct_vague) : "-";

  renderBars($("#chart-macro"), named(select(f.period, "all", "macro_sector"), "macro_sector").sort(byPct),
    { highlight: f.macro !== "all" ? f.macro : null });
  renderBars($("#chart-region"), named(select(f.period, f.macro, "region"), "region").sort(byPct));
  const byFunction = named(select(f.period, f.macro, "job_function"), "job_function")
    .sort((a, b) => b.ral_max_median - a.ral_max_median);
  const bySeniority = named(select(f.period, f.macro, "seniority").filter((r) => r.value !== "not_stated"), "seniority")
    .sort((a, b) => SENIORITY_ORDER.indexOf(a.value) - SENIORITY_ORDER.indexOf(b.value));
  const salaryScale = salaryDomain([...byFunction, ...bySeniority]);
  renderDumbbell($("#chart-function"), byFunction, salaryScale);
  renderDumbbell($("#chart-seniority"), bySeniority, salaryScale);
  renderBars($("#chart-contract"),
    named(select(f.period, f.macro, "contract_type").filter((r) => r.value !== "not_stated"), "contract_type").sort(byPct));
  renderBars($("#chart-period"),
    named(select("all", f.macro, "posting_period").filter((r) => r.value !== "not_stated"), "posting_period")
      .sort((a, b) => PERIOD_ORDER.indexOf(a.value) - PERIOD_ORDER.indexOf(b.value)));

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
  const macroSelect = $('#filters [name="macro"]');
  Object.entries(labels.macro_sector)
    .filter(([code]) => cells.some((c) => c.macro === code))
    .forEach(([code, name]) => macroSelect.append(new Option(name, code)));
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
