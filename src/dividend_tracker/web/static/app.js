/* ============================================================
   Dividend Tracker — frontend logika (vanilla JS, žádné závislosti)
   Srovnávací tabulka + detailní karty.
   ============================================================ */

"use strict";

const $ = (sel) => document.querySelector(sel);

const form = $("#analyze-form");
const input = $("#ticker-input");
const btn = $("#analyze-btn");
const emptyState = $("#empty-state");
const progressEl = $("#progress");
const resultsEl = $("#results");
const progressTitle = $("#progress-title");
const progressSub = $("#progress-sub");
const tableSection = $("#table-section");
const tableBody = $("#table-body");
const tableCount = $("#table-count");
const watchlistSection = $("#watchlist-section");
const watchlistBody = $("#watchlist-body");
const watchlistCount = $("#watchlist-count");
const syncBtn = $("#sync-btn");
const scanBtn = $("#scan-btn");
const scanSection = $("#scan-section");
const scanResults = $("#scan-results");
const scanCount = $("#scan-count");
const fmpBar = $("#fmp-bar");
const fmpStatusText = $("#fmp-status-text");
const refreshFmpBtn = $("#refresh-fmp-btn");
const pfSection = $("#portfolio-section");
const pfBody = $("#portfolio-body");
const pfCount = $("#portfolio-count");
const pfSummary = $("#portfolio-summary");
const targetBox = $("#income-target-box");
const targetLabel = $("#target-progress-label");
const targetBar = $("#target-progress-bar");
const targetDetail = $("#target-detail");
const alertsSection = $("#alerts-section");
const alertsBody = $("#alerts-body");
const alertsCount = $("#alerts-count");
const alertForm = $("#alert-form");
const alertTickerInput = $("#alert-ticker");
const alertConditionSel = $("#alert-condition");
const alertThresholdInput = $("#alert-threshold");
const exportWlBtn = $("#export-wl-btn");
const exportAnalysisBtn = $("#export-analysis-btn");
const wlAddForm = $("#wl-add-form");
const wlAddTickerInput = $("#wl-add-ticker");
const pfAddForm = $("#pf-add-form");
const pfTickerInput = $("#pf-ticker");
const pfSharesInput = $("#pf-shares");
const pfCostInput = $("#pf-cost");
const incomeTargetForm = $("#income-target-form");
const incomeTargetInput = $("#income-target-input");
const incomeTargetClearBtn = $("#income-target-clear");

let alertRows = [];

let pollTimer = null;
let sortKey = "score";      // výchozí řazení: nejlepší skóre nahoře
let sortDir = -1;           // -1 = sestupně, 1 = vzestupně
let currentRows = [];       // poslední vyrenderované řádky (pro přeseřazení bez fetche)

/* watchlist (DB srovnání) — výchozí: abecedně vzestupně */
let wlSortKey = "ticker";
let wlSortDir = 1;
let wlRows = [];

/* ------------------------------ formátování ------------------------------ */

function fmtPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${Number(v).toFixed(digits)} %`;
}

function fmtX(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${Number(v).toFixed(digits)}×`;
}

function fmtMoney(v, currency = "USD") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sym = currency === "EUR" ? "€" : currency === "GBP" ? "£" : "$";
  return `${sym}${Number(v).toLocaleString("cs-CZ", { maximumFractionDigits: 2 })}`;
}

function fmtInt(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("cs-CZ");
}

/* ------------------------------ hodnocení ------------------------------ */

function scoreColor(score) {
  if (score >= 70) return getComputedStyle(document.documentElement).getPropertyValue("--green").trim();
  if (score >= 50) return getComputedStyle(document.documentElement).getPropertyValue("--yellow").trim();
  return getComputedStyle(document.documentElement).getPropertyValue("--red").trim();
}

function scoreClass(score) {
  if (score >= 70) return "val-good";
  if (score >= 50) return "val-warn";
  return "val-bad";
}

const FLAG_LABELS = { green: "🟢 Bezpečná", yellow: "🟡 Pozor", red: "🔴 Riziková" };

/* ------------------------------ tabulka ------------------------------ */

/** Vytáhne řádek s hodnotami pro seřazení z reportu (nebo chyby). */
function rowFromEntry(entry) {
  if (entry.error) {
    return { ticker: entry.ticker, name: "", error: true, price: null, yieldPct: null, payout: null, growth5: null, streak: null, score: null, fv: null, zoneLow: null, zoneHigh: null, distPct: null };
  }
  const r = entry.report;
  const d = (r.metrics || {}).dividend || {};
  const score = r.score || {}, fv = r.fair_value || {};
  const isReit = /real estate/i.test((r.company || {}).sector || "");
  const payout = isReit ? (d.affo_payout_ratio_pct ?? d.ffo_payout_ratio_pct) : d.payout_ratio_eps_pct;

  let distPct = null; // kolik % musí cena klesnout, aby vstoupila do nákupní zóny
  if (r.price_current != null && fv.buy_zone_high != null) {
    distPct = ((r.price_current - fv.buy_zone_high) / r.price_current) * 100; // <= 0 → už v zóně
  }

  return {
    ticker: r.ticker,
    name: (r.company || {}).name || "",
    error: false,
    price: r.price_current,
    yieldPct: d.yield_trailing_pct,
    payout,
    growth5: d.growth_cagr_5y_pct,
    streak: d.dividend_streak_years ?? null,
    score: score.applicable === false ? null : (score.total_0_100 ?? null),
    fv: fv.fair_value ?? null,
    zoneLow: fv.buy_zone_low ?? null,
    zoneHigh: fv.buy_zone_high ?? null,
    distPct,
  };
}

function sortValue(row) {
  switch (sortKey) {
    case "ticker": return row.ticker;
    case "price": return row.price;
    case "yield": return row.yieldPct;
    case "payout": return row.payout;
    case "growth5": return row.growth5;
    case "streak": return row.streak;
    case "score": return row.score;
    case "fv": return row.fv;
    case "zone": return row.zoneHigh;
    case "dist": return row.distPct == null ? Infinity : row.distPct; // nejblíž zóně nahoře
    default: return 0;
  }
}

function renderTable(rows) {
  currentRows = rows;
  const sorted = [...rows].sort((a, b) => {
    const va = sortValue(a), vb = sortValue(b);
    if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
    if (va == null) return 1;   // chybějící hodnoty vždy dolů
    if (vb == null) return -1;
    if (typeof va === "string") return sortDir * va.localeCompare(vb);
    return sortDir * (va - vb);
  });

  tableBody.innerHTML = sorted.map((row) => {
    const inZone = row.distPct != null && row.distPct <= 0;
    let distCell, distCls;
    if (row.error) { distCell = "—"; distCls = ""; }
    else if (inZone) { distCell = `<span class="badge badge-green">v zóně</span>`; distCls = ""; }
    else if (row.distPct == null) { distCell = "—"; distCls = ""; }
    else {
      const cls = row.distPct <= 15 ? "dist-watch" : "dist-far";
      distCell = `<span class="${cls}">−${row.distPct.toFixed(1)} %</span>`;
    }

    const scoreCell = row.score == null
      ? (row.error ? "⚠️" : "—")
      : `<span class="badge badge-${scoreClass(row.score) === "val-good" ? "green" : scoreClass(row.score) === "val-warn" ? "yellow" : "red"}">${Math.round(row.score)}</span>`;

    return `<tr data-ticker="${escapeHtml(row.ticker)}" class="${inZone ? "in-zone" : ""}">
      <td><span class="td-ticker">${escapeHtml(row.ticker)}</span>${row.name ? `<span class="td-sub">${escapeHtml(row.name)}</span>` : ""}</td>
      <td class="num">${fmtMoney(row.price)}</td>
      <td class="num">${fmtPct(row.yieldPct)}</td>
      <td class="num">${fmtPct(row.payout)}</td>
      <td class="num">${fmtPct(row.growth5)}</td>
      <td class="num">${row.streak == null ? "—" : row.streak}</td>
      <td class="num">${scoreCell}</td>
      <td class="num">${fmtMoney(row.fv)}</td>
      <td class="num">${row.zoneLow != null && row.zoneHigh != null ? `${fmtMoney(row.zoneLow)} – ${fmtMoney(row.zoneHigh)}` : "—"}</td>
      <td class="num">${distCell}</td>
    </tr>`;
  }).join("");

  const nInZone = rows.filter((r) => !r.error && r.distPct != null && r.distPct <= 0).length;
  tableCount.textContent = `${rows.length} ticker(ů)` + (nInZone ? ` · ${nInZone} v nákupní zóně` : "");

  // indikátor aktuálního řazení
  document.querySelectorAll("#compare-table thead th").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.key === sortKey) th.classList.add(sortDir === -1 ? "sorted-desc" : "sorted-asc");
  });
}

/* ------------------------------ watchlist (DB srovnání) ------------------------------ */

/** Formátuje hodnotu buňky podle typu atributu. */
function wlCell(key, row) {
  const v = row[key];
  switch (key) {
    case "in_watchlist": {
      // ★ trvalá watchlista — kliknutím se přepne (delegace na tbody)
      return `<button type="button" class="wl-star-btn ${v ? "active" : ""}" data-ticker="${escapeHtml(row.ticker)}" title="${v ? "Odebrat z trvalé watchlisty" : "Přidat do trvalé watchlisty"}">${v ? "★" : "☆"}</button>`;
    }
    case "ticker": return `<span class="td-ticker">${escapeHtml(v)}</span>`;
    case "name": return escapeHtml(v || "—");
    case "sector": return escapeHtml(v || "—");
    case "price": return fmtMoney(v, row.currency);
    case "change": {
      if (v == null) return "—";
      const cls = v > 0 ? "val-good" : v < 0 ? "val-bad" : "";
      const sign = v > 0 ? "+" : "";
      return `<span class="${cls}">${sign}${Number(v).toFixed(2)}</span>`;
    }
    case "volume": return fmtInt(v);
    case "dps_ttm": return fmtMoney(v, row.currency);
    case "yield_pct": {
      if (v == null) return "—";
      const cls = v >= 5 ? "val-good" : v < 2.5 ? "val-warn" : "";
      return `<span class="${cls}">${fmtPct(v)}</span>`;
    }
    case "net_yield_pct": {
      if (v == null) return "—";
      const cls = v >= 4 ? "val-good" : v < 2 ? "val-warn" : "";
      return `<span class="${cls}">${fmtPct(v)}</span>`;
    }
    case "growth_5y_pct": {
      if (v == null) return "—";
      const cls = v > 0 ? "val-good" : v < 0 ? "val-bad" : "";
      const sign = v > 0 ? "+" : "";
      return `<span class="${cls}">${sign}${fmtPct(v)}</span>`;
    }
    case "streak_years": {
      if (v == null) return "—";
      const cls = v >= 10 ? "val-good" : "";
      return `<span class="${cls}">${v}</span>`;
    }
    case "next_ex_date": return escapeHtml(v || "—");
    case "next_dividend": return fmtMoney(v, row.currency);
    case "low_52w": return fmtMoney(v, row.currency);
    case "high_52w": return fmtMoney(v, row.currency);
    case "drawdown_from_high_pct": {
      if (v == null) return "—";
      // výrazný drawdown = potenciální příležitost → zeleně
      const cls = v <= -15 ? "val-good" : v < 0 ? "" : "";
      const sign = v > 0 ? "+" : "";
      return `<span class="${cls}">${sign}${fmtPct(v)}</span>`;
    }
    case "percentile_52w": {
      if (v == null) return "—";
      // nízký percentil = cena blízko 52t dna → zeleně
      const cls = v <= 20 ? "val-good" : "";
      return `<span class="${cls}">${Number(v).toFixed(1)}</span>`;
    }
    default: return v == null ? "—" : escapeHtml(String(v));
  }
}

function renderWatchlist() {
  const sorted = [...wlRows].sort((a, b) => {
    let va = a[wlSortKey], vb = b[wlSortKey];
    if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
    if (va == null) return 1;   // chybějící hodnoty vždy dolů
    if (vb == null) return -1;
    if (typeof va === "string") return wlSortDir * va.localeCompare(vb, "cs");
    return wlSortDir * (va - vb);
  });

  watchlistBody.innerHTML = sorted.map((row) => {
    const cells = ["in_watchlist", "ticker", "name", "sector", "price", "change", "volume", "dps_ttm",
      "yield_pct", "net_yield_pct", "growth_5y_pct", "streak_years", "next_ex_date", "next_dividend",
      "low_52w", "high_52w", "drawdown_from_high_pct", "percentile_52w"]
      .map((k) => `<td class="${/^(price|change|volume|dps_ttm|yield_pct|net_yield_pct|growth_5y_pct|streak_years|next_dividend|low_52w|high_52w|drawdown_from_high_pct|percentile_52w)$/.test(k) ? "num" : ""}">${wlCell(k, row)}</td>`)
      .join("");
    return `<tr data-ticker="${escapeHtml(row.ticker)}">${cells}</tr>`;
  }).join("");

  watchlistCount.textContent = `${wlRows.length} ticker(ů) v databáze`;

  // indikátor aktuálního řazení
  document.querySelectorAll("#watchlist-table thead th").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.key === wlSortKey) th.classList.add(wlSortDir === -1 ? "sorted-desc" : "sorted-asc");
  });
}

async function loadWatchlist() {
  try {
    const res = await fetch("/api/watchlist");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    wlRows = data.rows || [];
    if (wlRows.length) {
      watchlistSection.classList.remove("hidden");
      renderWatchlist();
      emptyState.classList.add("hidden");
      exportWlBtn.classList.remove("hidden"); // CSV export dostupný jen s daty v DB
    } else {
      watchlistSection.classList.add("hidden");
      emptyState.classList.remove("hidden");
      exportWlBtn.classList.add("hidden");
    }
  } catch {
    /* server nedostupný — zkusit znovu při dalším načtení */
  }
}

/* ------------------------------ trvalá watchlista (★) ------------------------------ */

/** Přepne ★/☆ u tickera v trvalé watchlistě a překreslí tabulku. */
async function toggleWatchlistStar(ticker) {
  const row = wlRows.find((r) => r.ticker === ticker);
  if (!row) return;
  try {
    let res;
    if (row.in_watchlist) {
      res = await fetch(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: "DELETE" });
    } else {
      res = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker }),
      });
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    row.in_watchlist = !row.in_watchlist; // optimistické překreslení bez celkového fetchu
    renderWatchlist();
  } catch {
    await loadWatchlist(); // server nedostupný → obnovit skutečný stav
  }
}

/** Přidání tickera do trvalé watchlisty (formulář nad tabulkou). */
async function submitWlAdd(e) {
  e.preventDefault();
  const ticker = wlAddTickerInput.value.trim().toUpperCase();
  if (!ticker) return;
  try {
    const res = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.error || `Chyba při přidávání do watchlisty (HTTP ${res.status})`);
      return;
    }
    wlAddTickerInput.value = "";
    await loadWatchlist(); // nový ticker se objeví po dalším syncu dat — ★ už ale svítí
  } catch {
    alert("Server nedostupný — watchlista se nepodařila aktualizovat.");
  }
}

/* ------------------------------ Portfolio ------------------------------ */

let pfRows = [];
let pfSortKey = "market_value";
let pfSortDir = -1;

function pfCell(key, row) {
  const v = row[key];
  switch (key) {
    case "ticker": return `<strong>${escapeHtml(v)}</strong>`;
    case "shares": return fmtInt(v);
    case "avg_cost": return fmtMoney(v, row.currency);
    case "price": return fmtMoney(v, row.currency);
    case "market_value": return fmtMoney(v, row.currency);
    case "unrealized_pl_pct": {
      if (v == null) return "—";
      const cls = v > 0 ? "val-good" : v < 0 ? "val-bad" : "";
      const sign = v > 0 ? "+" : "";
      return `<span class="${cls}">${sign}${fmtPct(v)}</span>`;
    }
    case "dps_ttm": return fmtMoney(v, row.currency);
    case "annual_income": return fmtMoney(v, row.currency);
    case "net_annual_income": {
      if (v == null) return "—";
      const rate = row.withholding_rate_pct;
      const title = rate ? `po sražení ${rate} % zadržovací daně` : "";
      return `<span class="val-net" title="${title}">${fmtMoney(v, row.currency)}</span>`;
    }
    case "yield_on_cost_pct": {
      if (v == null) return "—";
      const cls = v >= 5 ? "val-good" : "";
      return `<span class="${cls}">${fmtPct(v)}</span>`;
    }
    default: return v == null ? "—" : escapeHtml(String(v));
  }
}

function renderPortfolio() {
  const sorted = [...pfRows].sort((a, b) => {
    let va = a[pfSortKey], vb = b[pfSortKey];
    if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "string") return pfSortDir * va.localeCompare(vb, "cs");
    return pfSortDir * (va - vb);
  });

  pfBody.innerHTML = sorted.map((row) => {
    const cells = ["ticker", "shares", "avg_cost", "price", "market_value",
      "unrealized_pl_pct", "dps_ttm", "annual_income", "net_annual_income", "yield_on_cost_pct"]
      .map((k) => `<td class="${/^(shares|avg_cost|price|market_value|unrealized_pl_pct|dps_ttm|annual_income|net_annual_income|yield_on_cost_pct)$/.test(k) ? "num" : ""}">${pfCell(k, row)}</td>`)
      .join("");
    const removeBtn = `<td><button type="button" class="alert-remove-btn pf-remove-btn" data-ticker="${escapeHtml(row.ticker)}" title="Smazat pozici">✕</button></td>`;
    return `<tr data-ticker="${escapeHtml(row.ticker)}">${cells}${removeBtn}</tr>`;
  }).join("");

  pfCount.textContent = `${pfRows.length} pozic`;
  document.querySelectorAll("#portfolio-table thead th").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.key === pfSortKey) th.classList.add(pfSortDir === -1 ? "sorted-desc" : "sorted-asc");
  });
}

function renderPortfolioSummary(p, t) {
  const stats = [
    ["Hodnota portfolia", p.total_market_value != null ? fmtMoney(p.total_market_value) : "—"],
    ["Roční příjem", p.annual_income > 0 ? `${fmtMoney(p.annual_income)} / rok` : "—"],
    ["Čistý příjem (po zdanění)", p.net_annual_income > 0 ? `${fmtMoney(p.net_annual_income)} / rok` : "—"],
    ["Portfolio yield", p.portfolio_yield_pct != null ? fmtPct(p.portfolio_yield_pct) : "—"],
    ["Net portfolio yield", p.net_portfolio_yield_pct != null ? fmtPct(p.net_portfolio_yield_pct) : "—"],
    ["Yield on cost", p.weighted_yield_on_cost_pct != null ? fmtPct(p.weighted_yield_on_cost_pct) : "—"],
  ];
  pfSummary.innerHTML = stats.map(([label, value]) =>
    `<div class="pf-stat"><div class="pf-label">${label}</div><div class="pf-value">${value}</div></div>`).join("");

  if (t && t.configured) {
    targetBox.classList.remove("hidden");
    const pct = Math.min(100, Math.max(0, t.progress_pct ?? 0));
    targetBar.style.width = `${pct}%`;
    targetLabel.textContent = `${fmtMoney(t.current_annual_income)} / ${fmtMoney(t.target_annual_income)} (${pct} %)`;
    const parts = [];
    if (t.remaining_income > 0) parts.push(`chybí ${fmtMoney(t.remaining_income)}/rok`);
    else parts.push("cíl dosažen 🎉");
    if (t.required_capital != null) {
      parts.push(`potřebný kapitál ~${fmtMoney(t.required_capital)} (yield ${fmtPct(t.effective_yield_pct_used)})`);
    }
    targetDetail.textContent = parts.join(" · ");
  } else {
    targetBox.classList.add("hidden");
  }
}

async function loadPortfolio() {
  try {
    const res = await fetch("/api/portfolio");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pfRows = (data.portfolio && data.portfolio.positions) || [];
    const targetConfigured = !!(data.income_target && data.income_target.configured);
    if (pfRows.length || targetConfigured) {
      pfSection.classList.remove("hidden");
      renderPortfolioSummary(data.portfolio, data.income_target);
      renderPortfolio();
    } else {
      pfSection.classList.add("hidden");
    }
  } catch {
    /* server nedostupný — zkusit znovu při dalším načtení */
  }
}

/* ------------------------------ portfolio: pozice + cíl příjmu ------------------------------ */

/** Přidá/aktualizuje drženou pozici (formulář v sekci Portfolio). */
async function submitPfAdd(e) {
  e.preventDefault();
  const ticker = pfTickerInput.value.trim().toUpperCase();
  if (!ticker) return;
  const sharesRaw = pfSharesInput.value;
  if (sharesRaw === "") return;
  const payload = { ticker, shares: Number(sharesRaw) };
  const costRaw = pfCostInput.value;
  if (costRaw !== "") payload.avg_cost = Number(costRaw);
  try {
    const res = await fetch("/api/portfolio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.error || `Chyba při přidávání pozice (HTTP ${res.status})`);
      return;
    }
    pfTickerInput.value = "";
    pfSharesInput.value = "";
    pfCostInput.value = "";
    await loadPortfolio();
  } catch {
    alert("Server nedostupný — pozice se nepodařila přidat.");
  }
}

/** Smazá drženou pozici (tlačítko ✕ v řádku tabulky). */
async function removePfPosition(ticker) {
  try {
    const res = await fetch(`/api/portfolio/${encodeURIComponent(ticker)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 405) throw new Error(`HTTP ${res.status}`);
    pfRows = pfRows.filter((r) => r.ticker !== ticker);
    renderPortfolio();
    await loadPortfolio(); // obnovit souhrn (hodnota, příjem, cíl)
  } catch {
    await loadPortfolio();
  }
}

/** Uloží cíl ročního příjmu (formulář). */
async function submitIncomeTarget(e) {
  e.preventDefault();
  const raw = incomeTargetInput.value;
  if (raw === "") return;
  try {
    const res = await fetch("/api/income-target", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: Number(raw) }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.error || `Chyba při ukládání cíle (HTTP ${res.status})`);
      return;
    }
    incomeTargetInput.value = "";
    await loadPortfolio();
  } catch {
    alert("Server nedostupný — cíl se nepodařilo uložit.");
  }
}

/** Odebere cíl ročního příjmu (tlačítko „Odebrat cíl“). */
async function clearIncomeTarget() {
  try {
    const res = await fetch("/api/income-target", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: 0 }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadPortfolio();
  } catch {
    alert("Server nedostupný — cíl se nepodařilo odebrat.");
  }
}

/* ------------------------------ Upozornění (alerts) ------------------------------ */

const ALERT_CONDITION_LABELS = { below: "klesne pod", above: "vystoupá nad", buy_zone: "nákupní zóna" };

function renderAlerts() {
  if (!alertRows.length) {
    alertsSection.classList.add("hidden");
    return;
  }
  alertsSection.classList.remove("hidden");
  alertsCount.textContent = `${alertRows.length} pravidel`;
  const sorted = [...alertRows].sort((a, b) => a.ticker.localeCompare(b.ticker));
  alertsBody.innerHTML = sorted.map((r) => {
    const thr = r.threshold != null ? fmtMoney(r.threshold) : "—";
    const price = r.price != null ? fmtMoney(r.price) : "—";
    // cena už splňuje podmínku → zvýraznit (příležitost!)
    let hit = false;
    if (r.active && r.price != null) {
      if (r.condition === "below" && r.threshold != null) hit = r.price <= r.threshold;
      else if (r.condition === "above" && r.threshold != null) hit = r.price >= r.threshold;
    }
    const cls = hit ? ' class="buy-zone-row"' : "";
    return `<tr${cls}>
      <td><strong>${escapeHtml(r.ticker)}</strong></td>
      <td>${ALERT_CONDITION_LABELS[r.condition] || escapeHtml(r.condition)}${r.active ? "" : " (vypnuto)"}</td>
      <td class="num">${thr}</td>
      <td class="num">${price}</td>
      <td>${escapeHtml(r.last_triggered || "—")}</td>
      <td><button type="button" class="alert-remove-btn" data-alert-id="${r.id}" title="Smazat pravidlo">✕</button></td>
    </tr>`;
  }).join("");
}

async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    alertRows = data.alerts || [];
    renderAlerts();
  } catch {
    /* server nedostupný — zkusit znovu při dalším načtení */
  }
}

async function submitAlert(e) {
  e.preventDefault();
  const ticker = alertTickerInput.value.trim().toUpperCase();
  if (!ticker) return;
  const condition = alertConditionSel.value;
  const thresholdRaw = alertThresholdInput.value;
  const payload = { ticker, condition };
  if (condition !== "buy_zone" && thresholdRaw !== "") payload.threshold = Number(thresholdRaw);
  try {
    const res = await fetch("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.error || `Chyba při přidávání upozornění (HTTP ${res.status})`);
      return;
    }
    alertTickerInput.value = "";
    alertThresholdInput.value = "";
    await loadAlerts();
  } catch {
    alert("Server nedostupný — upozornění se nepodařilo přidat.");
  }
}

async function removeAlert(id) {
  try {
    const res = await fetch(`/api/alerts/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 405) throw new Error(`HTTP ${res.status}`);
    alertRows = alertRows.filter((r) => r.id !== id);
    renderAlerts();
  } catch {
    await loadAlerts(); // server nepodporuje DELETE → alespoň překreslit aktuální stav
  }
}

/* ------------------------------ FMP status ------------------------------ */

async function loadFmpStatus() {
  try {
    const res = await fetch("/api/fmp-status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();
    fmpBar.classList.remove("hidden");
    if (!s.configured) {
      fmpStatusText.textContent = "FMP: API klíč nenastaven (proměnná FMP_API_KEY) — fundamenty se stahují přes yfinance fallback.";
      return;
    }
    const limit = s.daily_limit ?? 250;
    const used = s.calls_used ?? 0;
    const remaining = Math.max(0, (s.remaining != null ? s.remaining : limit - used));
    fmpStatusText.textContent = `FMP: ${used}/${limit} volání dnes · zbývá ${remaining}` +
      (remaining <= 25 ? " ⚠️ limit se blíží — fundamenty se už netaží, čtu z DB" : "");
  } catch {
    fmpBar.classList.add("hidden");
  }
}

/* ------------------------------ synchronizace dat ------------------------------ */

async function startSync(tickers) {
  setBusy(true);
  progressEl.classList.remove("hidden");
  progressTitle.textContent = `Synchronizuji data pro ${tickers.length} ticker(ů)…`;
  progressSub.textContent = "Cache-first: čerstvá data z lokální DB, stará se stahují (FMP limit 250/den).";

  try {
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers }),
    });
    if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
    const { job_id } = await res.json();
    pollSyncJob(job_id, tickers.length);
  } catch (err) {
    showError(`Synchronizaci se nepodařilo spustit: ${err.message}`);
  }
}

function pollSyncJob(jobId, total) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const job = await res.json();

      progressTitle.textContent = `Synchronizuji… (${job.done}/${total})`;

      if (job.status === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        progressEl.classList.add("hidden");
        setBusy(false);
        const entry = job.results[0] || {};
        if (entry.error) {
          showError(entry.error);
          return;
        }
        const s = entry.summary || {};
        const c = s.counts || {};
        const q = s.quotes || {};
        alert(
          `Synchronizace hotová:\n` +
          `• z cache: ${c.cached ?? 0}\n` +
          `• FMP: ${c.fmp ?? 0} · yfinance fallback: ${c.yfinance ?? 0}\n` +
          `• stará data ponechána: ${c.stale_cache ?? 0} · chyby: ${c.error ?? 0}\n` +
          `• ceny aktualizovány: ${q.updated ?? 0}/${total}`
        );
        await Promise.all([loadWatchlist(), loadFmpStatus()]);
      }
    } catch {
      /* dočasná chyba sítě — zkusit znovu při dalším ticku */
    }
  }, 2000);
}

/* ------------------------------ scan — nákupní příležitosti ------------------------------ */

async function startScan(tickers) {
  setBusy(true);
  progressEl.classList.remove("hidden");
  const scope = tickers.length ? `${tickers.length} ticker(ů)` : "celou trvalou watchlistu";
  progressTitle.textContent = `Skenuji ${scope}…`;
  progressSub.textContent = "Hledám akce se zeleným safety flagem a signálem BUY_ZONE (jako divtrack scan).";

  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(tickers.length ? { tickers } : {}),
    });
    if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
    const { job_id } = await res.json();
    pollScanJob(job_id);
  } catch (err) {
    showError(`Scan se nepodařilo spustit: ${err.message}`);
  }
}

function pollScanJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  const rendered = new Set();
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const job = await res.json();

      // Detailní karty — postupně, jen nové položky (stejně jako analýza).
      for (const entry of job.results) {
        if (rendered.has(entry.ticker)) continue;
        rendered.add(entry.ticker);
        if (!entry.error && !document.getElementById(`card-${entry.ticker}`)) {
          resultsEl.insertAdjacentHTML("beforeend", `<div id="card-${entry.ticker}">${renderTickerCard(entry)}</div>`);
          animateRing(document.querySelector(`#card-${entry.ticker} .ring-fg`));
        }
      }

      progressTitle.textContent = `Skenuji… (${job.done}/${job.total})`;

      // Hotovo až po doplnění matches (server je počítá až po dokončení všech analýz).
      if (job.status === "done" && job.matches !== undefined) {
        clearInterval(pollTimer);
        pollTimer = null;
        progressEl.classList.add("hidden");
        setBusy(false);
        renderScanResults(job);
      }
    } catch {
      /* dočasná chyba sítě — zkusit znovu při dalším ticku */
    }
  }, 1500);
}

function renderScanResults(job) {
  const matches = job.matches || [];
  scanSection.classList.remove("hidden");
  if (!matches.length) {
    scanCount.textContent = "";
    scanResults.innerHTML = `<p class="scan-empty">Žádná akce ze ${job.total} analyzovaných nesplňuje zelený flag + BUY_ZONE.</p>`;
    return;
  }
  scanCount.textContent = `${matches.length} z ${job.total}`;
  const byTicker = new Map(job.results.filter((e) => e.report).map((e) => [e.ticker, e]));
  scanResults.innerHTML = matches.map((t) => {
    const entry = byTicker.get(t);
    if (!entry) return "";
    const r = entry.report;
    const d = (r.metrics || {}).dividend || {};
    const fv = r.fair_value || {};
    const price = r.price_current;
    let distCell = "—";
    if (price != null && fv.buy_zone_high != null) {
      const distPct = ((price - fv.buy_zone_high) / price) * 100;
      distCell = distPct <= 0 ? `<span class="badge badge-green">v zóně</span>` : `−${distPct.toFixed(1)} %`;
    }
    const zone = fv.buy_zone_low != null && fv.buy_zone_high != null
      ? `${fmtMoney(fv.buy_zone_low, r.currency)}–${fmtMoney(fv.buy_zone_high, r.currency)}`
      : "—";
    return `<div class="scan-row" data-ticker="${escapeHtml(t)}">
      <span class="td-ticker">${escapeHtml(t)}</span>
      <span>${fmtMoney(price, r.currency)}</span>
      <span>${fmtPct(d.yield_trailing_pct)}</span>
      <span>${fmtMoney(fv.fair_value, r.currency)}</span>
      <span>${zone}</span>
      ${distCell}
    </div>`;
  }).join("");
}

/* ------------------------------ renderování karet ------------------------------ */

function tile(label, value, sub = "", cls = "") {
  return `<div class="metric-tile">
    <div class="metric-label">${label}</div>
    <div class="metric-value ${cls}">${value}</div>
    ${sub ? `<div class="metric-sub">${sub}</div>` : ""}
  </div>`;
}

function scoreRing(score) {
  const R = 42, C = 2 * Math.PI * R;
  const offset = C - (Math.max(0, Math.min(100, score)) / 100) * C;
  return `<div class="score-ring">
    <svg width="96" height="96" viewBox="0 0 96 96">
      <circle class="ring-bg" cx="48" cy="48" r="${R}" fill="none" stroke-width="8"/>
      <circle class="ring-fg" cx="48" cy="48" r="${R}" fill="none" stroke-width="8"
        stroke-linecap="round" stroke="${scoreColor(score)}"
        stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${C.toFixed(1)}" data-target="${offset.toFixed(1)}"/>
    </svg>
    <div class="score-value">${Math.round(score)}</div>
  </div>`;
}

function renderTickerCard(entry) {
  if (entry.error) {
    return `<article class="card error-card">
      <span class="error-icon">⚠️</span>
      <div><h3>${escapeHtml(entry.ticker)}</h3><p>${escapeHtml(entry.error)}</p></div>
    </article>`;
  }

  const r = entry.report;
  const m = r.metrics || {};
  const d = m.dividend || {}, v = m.valuation || {}, h = m.health || {};
  const score = r.score || {}, fv = r.fair_value || {};

  /* --- skóre sekce --- */
  let scoreHtml;
  if (score.applicable === false) {
    scoreHtml = `<div class="score-row">
      <div class="score-details"><div class="score-label">Skóre</div>
        <p style="font-size:14px;color:var(--text-secondary)">Společnost nevyplácí dividendu — skóre se nepoužilo.</p></div>
    </div>`;
  } else {
    const flag = score.safety_flag ? `<span class="flag-pill flag-${score.safety_flag}">${FLAG_LABELS[score.safety_flag] || score.safety_flag}</span>` : "";
    const signals = (score.signals_display || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    const reasons = (score.reasons || []).slice(0, 4).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    scoreHtml = `<div class="score-row">
      ${scoreRing(score.total_0_100 ?? 0)}
      <div class="score-details">
        <div class="score-label">Skóre dividendového zdraví</div>
        ${flag}
        <ul class="signal-list">${signals}${reasons}</ul>
      </div>
    </div>`;
  }

  /* --- dividendy --- */
  const isReit = /real estate/i.test(r.company?.sector || "");
  const payoutLabel = isReit ? "FFO/AFFO payout" : "Payout ratio (EPS)";
  const payoutVal = isReit
    ? (d.affo_payout_ratio_pct ?? d.ffo_payout_ratio_pct)
    : d.payout_ratio_eps_pct;
  const payoutCls = payoutVal == null ? "" : payoutVal > 100 ? "val-bad" : payoutVal < 75 ? "val-good" : "";

  const dividendHtml = `<div class="metric-section">
    <div class="section-title">Dividendy</div>
    <div class="metric-grid">
      ${tile("Yield (trailing)", fmtPct(d.yield_trailing_pct), `forward: ${fmtPct(d.yield_forward_pct)}`)}
      ${tile("DPS TTM", fmtMoney(d.dps_ttm, r.currency))}
      ${tile(payoutLabel, fmtPct(payoutVal), isReit ? "REIT logika" : "", payoutCls)}
      ${tile("Růst 3/5/10 let", [d.growth_cagr_3y_pct, d.growth_cagr_5y_pct, d.growth_cagr_10y_pct].map((x) => fmtPct(x)).join(" / "))}
      ${tile("Série růstu výplat", `${fmtInt(d.dividend_streak_years)} let`)}
    </div>
  </div>`;

  /* --- valuace --- */
  const valuationHtml = `<div class="metric-section">
    <div class="section-title">Ocenění</div>
    <div class="metric-grid">
      ${tile("P/E (TTM)", fmtX(v.pe_ttm), v.sector_median_pe_ttm != null ? `sektor: ${fmtX(v.sector_median_pe_ttm)}` : "")}
      ${tile("P/E (forward)", fmtX(v.pe_forward))}
      ${tile("EV/EBITDA", fmtX(v.ev_ebitda), v.sector_median_ev_ebitda != null ? `sektor: ${fmtX(v.sector_median_ev_ebitda)}` : "")}
      ${tile("P/FCF", fmtX(v.p_fcf))}
      ${tile("FCF yield", fmtPct(v.fcf_yield_pct))}
      ${tile("Yield vs. 5letý průměr", fmtPct(d.yield_trailing_pct), v.dividend_yield_5y_avg_pct != null ? `průměr: ${fmtPct(v.dividend_yield_5y_avg_pct)}` : "")}
    </div>
  </div>`;

  /* --- zdraví --- */
  const ndCls = h.net_debt_to_ebitda == null ? "" : h.net_debt_to_ebitda > 3 ? "val-bad" : h.net_debt_to_ebitda < 1.5 ? "val-good" : "";
  const healthHtml = `<div class="metric-section">
    <div class="section-title">Finanční zdraví</div>
    <div class="metric-grid">
      ${tile("Net Debt / EBITDA", fmtX(h.net_debt_to_ebitda), "", ndCls)}
      ${tile("Interest Coverage", fmtX(h.interest_coverage))}
      ${tile("ROIC", fmtPct(h.roic_pct))}
    </div>
  </div>`;

  /* --- férová cena --- */
  let fvHtml = "";
  if (fv.fair_value != null) {
    const lo = Math.min(fv.buy_zone_low ?? fv.fair_value, r.price_current ?? fv.fair_value);
    const hi = Math.max(fv.buy_zone_high ?? fv.fair_value, r.price_current ?? fv.fair_value);
    const span = hi - lo || 1;
    const zoneLeft = ((fv.buy_zone_low ?? lo) - lo) / span * 100;
    const zoneWidth = Math.max(2, (((fv.buy_zone_high ?? fv.buy_zone_low ?? lo) - (fv.buy_zone_low ?? lo)) / span) * 100);
    const pricePos = ((r.price_current ?? lo) - lo) / span * 100;
    const verdictCls = fv.verdict === "undervalued" ? "flag-green" : fv.verdict === "overvalued" ? "flag-red" : "flag-yellow";

    fvHtml = `<div class="metric-section fv-band-wrap">
      <div class="section-title">Férová cena &amp; nákupní zóna</div>
      <span class="verdict-pill ${verdictCls}">${escapeHtml(fv.verdict_display || fv.verdict)}</span>
      <div class="fv-scale">
        <div class="fv-zone" style="left:${zoneLeft.toFixed(1)}%;width:${zoneWidth.toFixed(1)}%"></div>
        <div class="fv-marker" style="left:${pricePos.toFixed(1)}%" title="Aktuální cena"></div>
      </div>
      <div class="fv-labels">
        <span>${fmtMoney(lo, r.currency)}</span>
        <span>Nákupní zóna: ${fmtMoney(fv.buy_zone_low, r.currency)} – ${fmtMoney(fv.buy_zone_high, r.currency)}</span>
        <span>FV: ${fmtMoney(fv.fair_value, r.currency)}</span>
      </div>
    </div>`;
  }

  /* --- grafy (lazy-load z lokální DB) --- */
  const chartsHtml = `<div class="metric-section">
    <div class="section-title">Grafy</div>
    <div class="chart-block" id="price-chart-${escapeHtml(r.ticker)}"><p class="chart-empty">Načítám historii cen…</p></div>
    <div class="chart-block" id="dividend-chart-${escapeHtml(r.ticker)}"><p class="chart-empty">Načítám historii dividend…</p></div>
  </div>`;

  const gaps = (r.data_gaps || []).length ? `<p style="font-size:12px;color:var(--text-secondary);padding:0 28px 16px">Chybějící data: ${escapeHtml(r.data_gaps.join(", "))}</p>` : "";

  return `<article class="card ticker-card" id="detail-${escapeHtml(r.ticker)}">
    <div class="ticker-head">
      <div class="ticker-id">
        <span class="ticker-symbol">${escapeHtml(r.ticker)}</span>
        <div>
          <div class="ticker-name">${escapeHtml(r.company?.name || "")}</div>
          <div class="ticker-meta">${escapeHtml([r.company?.sector, r.company?.industry].filter(Boolean).join(" · "))}</div>
        </div>
      </div>
      <div class="ticker-price-block">
        <div class="ticker-price">${fmtMoney(r.price_current, r.currency)}</div>
        <div class="ticker-meta">${escapeHtml(new Date(r.generated_at).toLocaleDateString("cs-CZ"))}</div>
      </div>
      <button type="button" class="report-remove-btn" data-ticker="${escapeHtml(r.ticker)}" title="Smazat uložený report">✕</button>
      </div>
    ${scoreHtml}
    ${dividendHtml}
    ${valuationHtml}
    ${healthHtml}
    ${fvHtml}
    ${chartsHtml}
    ${gaps}
  </article>`;
}

/* ------------------------------ grafy (vanilla SVG, lazy-load) ------------------------------ */

const CHART_W = 720;   // viewBox šířka — SVG se škáluje na šířku karty
const CHART_H = 210;
const CHART_PAD = { top: 14, right: 14, bottom: 26, left: 52 };

function _chartScale(values) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || Math.abs(hi) * 0.1 || 1; // plochá řada → malý margin, aby nebyl graf degenerovaný
  return { lo: lo - span * 0.08, hi: hi + span * 0.08 };
}

/** Line chart denních cen (posledních ~365 dní). ``prices`` = [{date, close}] starší→novější. */
function priceChartSvg(prices) {
  const closes = prices.map((p) => p.close);
  if (closes.length < 2) return `<p class="chart-empty">Historie cen zatím má méně než 2 body — spusťte synchronizaci.</p>`;

  const { lo, hi } = _chartScale(closes);
  const iw = CHART_W - CHART_PAD.left - CHART_PAD.right;
  const ih = CHART_H - CHART_PAD.top - CHART_PAD.bottom;
  const x = (i) => CHART_PAD.left + (i / (closes.length - 1)) * iw;
  const y = (v) => CHART_PAD.top + (1 - (v - lo) / (hi - lo)) * ih;

  const line = closes.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(closes.length - 1).toFixed(1)},${(CHART_PAD.top + ih).toFixed(1)} L${x(0).toFixed(1)},${(CHART_PAD.top + ih).toFixed(1)} Z`;

  // 3 vodorovné mřížkové linky s popisky (max / střed / min)
  const grid = [hi, (hi + lo) / 2, lo].map((v) => {
    const gy = y(v).toFixed(1);
    return `<line class="chart-grid" x1="${CHART_PAD.left}" y1="${gy}" x2="${CHART_W - CHART_PAD.right}" y2="${gy}"/>
      <text class="chart-axis" x="${CHART_PAD.left - 8}" y="${Number(gy) + 4}" text-anchor="end">${v.toFixed(0)}</text>`;
  }).join("");

  const first = prices[0].date.slice(0, 7);   // RR-MM
  const last = prices[prices.length - 1].date.slice(0, 7);
  return `<svg class="chart" viewBox="0 0 ${CHART_W} ${CHART_H}" role="img" aria-label="Historie cen">
    ${grid}
    <path class="chart-area" d="${area}"/>
    <path class="chart-line" d="${line}"/>
    <circle class="chart-dot" cx="${x(closes.length - 1).toFixed(1)}" cy="${y(closes[closes.length - 1]).toFixed(1)}" r="3.5"/>
    <text class="chart-axis" x="${CHART_PAD.left}" y="${CHART_H - 8}">${first}</text>
    <text class="chart-axis" x="${CHART_W - CHART_PAD.right}" y="${CHART_H - 8}" text-anchor="end">${last}</text>
  </svg>`;
}

/** Sloupcový graf výplat (DPS) — posledních 12 plateb. ``dividends`` = [{ex_date, amount}] starší→novější. */
function dividendChartSvg(dividends) {
  const items = dividends.slice(-12);
  if (!items.length) return `<p class="chart-empty">Historie výplat zatím není v lokální DB — spusťte synchronizaci.</p>`;

  const maxAmt = Math.max(...items.map((d) => d.amount));
  const iw = CHART_W - CHART_PAD.left - CHART_PAD.right;
  const ih = CHART_H - CHART_PAD.top - CHART_PAD.bottom;
  const slot = iw / items.length;
  const barW = Math.min(46, slot * 0.62);

  const bars = items.map((d, i) => {
    const h = maxAmt ? (d.amount / maxAmt) * ih : 0;
    const bx = CHART_PAD.left + i * slot + (slot - barW) / 2;
    const by = CHART_PAD.top + ih - h;
    const label = d.ex_date.slice(5).replace("-", "/"); // MM/RR
    return `<rect class="chart-bar" x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(2, h).toFixed(1)}" rx="4"/>
      <text class="chart-axis chart-val" x="${(bx + barW / 2).toFixed(1)}" y="${(by - 6).toFixed(1)}" text-anchor="middle">${d.amount.toFixed(2)}</text>
      <text class="chart-axis" x="${(bx + barW / 2).toFixed(1)}" y="${CHART_H - 8}" text-anchor="middle">${label}</text>`;
  }).join("");

  return `<svg class="chart" viewBox="0 0 ${CHART_W} ${CHART_H}" role="img" aria-label="Historie výplat dividend">
    <line class="chart-grid" x1="${CHART_PAD.left}" y1="${(CHART_PAD.top + ih).toFixed(1)}" x2="${CHART_W - CHART_PAD.right}" y2="${(CHART_PAD.top + ih).toFixed(1)}"/>
    ${bars}
  </svg>`;
}

const _chartsLoaded = new Set(); // tickery, pro které už grafy načtené (ochrana proti dvojnásobnému fetchi)

/** Načte historii z lokální DB a vykreslí oba grafy v kartě tickera. */
async function loadCardCharts(ticker) {
  if (_chartsLoaded.has(ticker)) return;
  _chartsLoaded.add(ticker);
  const priceEl = document.getElementById(`price-chart-${ticker}`);
  const divEl = document.getElementById(`dividend-chart-${ticker}`);
  if (!priceEl || !divEl) return; // karta mezitím smazaná

  try {
    const res = await fetch(`/api/history/${encodeURIComponent(ticker)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    priceEl.innerHTML = priceChartSvg(data.prices || []);
    divEl.innerHTML = dividendChartSvg(data.dividends || []);
  } catch {
    // offline chyba — ukázat srozumitelný stav místo „Načítám…“ navždy
    const msg = `<p class="chart-empty">Grafy se nepodařilo načíst (DB nedostupná).</p>`;
    priceEl.innerHTML = msg;
    divEl.innerHTML = msg;
  }
}

/** Sleduje vklad nových karet do výsledků a spustí lazy-load grafů (funguje pro všechny cesty: analýza, scan, uložené reporty). */
function observeChartCards() {
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (!(node instanceof HTMLElement)) continue;
        const cards = node.matches?.(".ticker-card") ? [node] : [...(node.querySelectorAll?.(".ticker-card") || [])];
        for (const card of cards) {
          const sym = card.querySelector(".ticker-symbol");
          if (sym && sym.textContent) loadCardCharts(sym.textContent.trim());
        }
      }
    }
  });
  observer.observe(resultsEl, { childList: true, subtree: true });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------ uložené reporty (DB) ------------------------------ */

/** Načte perzistentní analytické reporty z lokální DB a vykreslí karty + srovnávací tabulku. */
async function loadStoredReports() {
  try {
    const res = await fetch("/api/reports");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const reports = data.reports || [];
    resultsEl.innerHTML = "";
    for (const report of reports) {
      const entry = { ticker: report.ticker, report };
      resultsEl.insertAdjacentHTML("beforeend", `<div id="card-${escapeHtml(report.ticker)}">${renderTickerCard(entry)}</div>`);
      animateRing(document.querySelector(`#card-${report.ticker} .ring-fg`));
    }
    if (reports.length) {
      tableSection.classList.remove("hidden");
      renderTable(reports.map((r) => rowFromEntry({ ticker: r.ticker, report: r })));
      emptyState.classList.add("hidden");
    } else {
      tableSection.classList.add("hidden");
      currentRows = [];
    }
  } catch {
    /* server nedostupný — zkusit znovu při dalším načtení */
  }
}

/** Smaže uložený report tickera (karta + řádek v tabulce). */
async function removeStoredReport(ticker) {
  try {
    const res = await fetch(`/api/reports/${encodeURIComponent(ticker)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 405) throw new Error(`HTTP ${res.status}`);
    await loadStoredReports();
  } catch {
    alert("Server nedostupný — report se nepodařilo smazat.");
  }
}

/* ------------------------------ API + polling ------------------------------ */

async function startAnalysis(tickers) {
  setBusy(true);
  resultsEl.innerHTML = "";
  tableBody.innerHTML = "";
  exportAnalysisBtn.classList.add("hidden"); // starý job už neplatí — export až po nových reportech
  emptyState.classList.add("hidden");
  progressEl.classList.remove("hidden");
  progressTitle.textContent = `Analyzuji ${tickers.length} ticker(ů)…`;
  progressSub.textContent = "Stahování dat z Yahoo Finance může trvat několik vteřin.";

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers }),
    });
    if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
    const { job_id } = await res.json();
    pollJob(job_id, tickers.length);
  } catch (err) {
    showError(`Analýzu se nepodařilo spustit: ${err.message}`);
  }
}

function pollJob(jobId, total) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const job = await res.json();

      // Detailní karty — postupně, jen nové položky.
      for (const entry of job.results) {
        const el = document.getElementById(`card-${entry.ticker}`);
        if (!el) {
          resultsEl.insertAdjacentHTML("beforeend", `<div id="card-${entry.ticker}">${renderTickerCard(entry)}</div>`);
          animateRing(document.querySelector(`#card-${entry.ticker} .ring-fg`));
        }
      }

      // Srovnávací tabulka — kompletně přerenderovat (drží seřazení).
      if (job.results.length) {
        tableSection.classList.remove("hidden");
        renderTable(job.results.map(rowFromEntry));
        // CSV export jen když je aspoň 1 hotový report (error položky se neexportují — server by vrátil 404).
        const hasReport = job.results.some((e) => e && "report" in e);
        if (hasReport) setAnalysisExport(jobId);
      }

      progressTitle.textContent = `Analyzuji… (${job.done}/${total})`;

      if (job.status === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        progressEl.classList.add("hidden");
        setBusy(false);
      }
    } catch {
      /* dočasná chyba sítě — zkusit znovu při dalším ticku */
    }
  }, 1500);
}

function setAnalysisExport(jobId) {
  // CSV export výsledků analýzy — odkaz na endpoint serveru (jen hotové reporty).
  if (!jobId || !exportAnalysisBtn) return;
  exportAnalysisBtn.href = `/api/export/analysis/${encodeURIComponent(jobId)}.csv`;
  exportAnalysisBtn.classList.remove("hidden");
}

function animateRing(circle) {
  if (!circle) return;
  requestAnimationFrame(() => { circle.style.strokeDashoffset = circle.dataset.target; });
}

function showError(msg) {
  progressEl.classList.add("hidden");
  tableSection.classList.add("hidden");
  resultsEl.innerHTML = `<article class="card error-card">
    <span class="error-icon">⚠️</span>
    <div><h3>Chyba</h3><p>${escapeHtml(msg)}</p></div>
  </article>`;
  setBusy(false);
}

function setBusy(busy) {
  btn.disabled = busy;
  input.disabled = busy;
  syncBtn.disabled = busy;
  scanBtn.disabled = busy;
}

/* ------------------------------ inicializace ------------------------------ */

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const tickers = [...new Set(input.value.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean))];
  if (!tickers.length) return;
  startAnalysis(tickers.slice(0, 100));
});

// Synchronizace dat do lokální DB (cache-first).
syncBtn.addEventListener("click", () => {
  const tickers = [...new Set(input.value.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean))];
  if (!tickers.length) return;
  startSync(tickers.slice(0, 100));
});

// Scan — nákupní příležitosti (zelený flag + BUY_ZONE), jako CLI `divtrack scan`.
// Prázdné pole = celá trvalá watchlista.
scanBtn.addEventListener("click", () => {
  const tickers = [...new Set(input.value.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean))];
  startScan(tickers.slice(0, 100));
});

// Klik na řádek výsledků scanu → scroll k detailní kartě.
scanResults.addEventListener("click", (e) => {
  const row = e.target.closest(".scan-row");
  if (!row) return;
  const card = document.getElementById(`card-${row.dataset.ticker}`);
  if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
});

// Seřazení watchlisty kliknutím na záhlaví (vzestupně/sestupně podle libovolného atributu).
document.querySelectorAll("#watchlist-table thead th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (wlSortKey === key) wlSortDir *= -1;
    else { wlSortKey = key; wlSortDir = typeof wlRows[0]?.[key] === "string" ? 1 : -1; }
    renderWatchlist();
  });
});

// Seřazení portfolia kliknutím na záhlaví.
document.querySelectorAll("#portfolio-table thead th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (pfSortKey === key) pfSortDir *= -1;
    else { pfSortKey = key; pfSortDir = typeof pfRows[0]?.[key] === "string" ? 1 : -1; }
    renderPortfolio();
  });
});

// Seřazení srovnávací tabulky analýzy.
document.querySelectorAll("#compare-table thead th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = key === "ticker" ? 1 : -1; }
    renderTable(currentRows);
  });
});

// Klik na řádek → skok na detailní kartu.
tableBody.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ticker]");
  if (!tr) return;
  const card = document.getElementById(`card-${tr.dataset.ticker}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.style.outline = "2px solid var(--accent)";
  setTimeout(() => { card.style.outline = ""; }, 1600);
});

refreshFmpBtn.addEventListener("click", loadFmpStatus);

// Přidání upozornění (formulář).
alertForm.addEventListener("submit", submitAlert);

// Trvalá watchlista — formulář + ★ toggle v řádku.
wlAddForm.addEventListener("submit", submitWlAdd);
watchlistBody.addEventListener("click", (e) => {
  const btnEl = e.target.closest(".wl-star-btn");
  if (!btnEl) return;
  toggleWatchlistStar(btnEl.dataset.ticker);
});

// Portfolio — přidání pozice, smazání pozice, cíl ročního příjmu.
pfAddForm.addEventListener("submit", submitPfAdd);
pfBody.addEventListener("click", (e) => {
  const btnEl = e.target.closest(".pf-remove-btn");
  if (!btnEl) return;
  removePfPosition(btnEl.dataset.ticker);
});
incomeTargetForm.addEventListener("submit", submitIncomeTarget);
incomeTargetClearBtn.addEventListener("click", clearIncomeTarget);

// Smazání pravidla — delegace na tlačítko v řádku.
alertsBody.addEventListener("click", (e) => {
  const btnEl = e.target.closest(".alert-remove-btn");
  if (!btnEl) return;
  removeAlert(Number(btnEl.dataset.alertId));
});

// Smazání uloženého reportu — delegace na tlačítko v detailní kartě.
resultsEl.addEventListener("click", (e) => {
  const btnEl = e.target.closest(".report-remove-btn");
  if (!btnEl) return;
  removeStoredReport(btnEl.dataset.ticker);
});

// Start: načíst watchlist z DB + portfolio + upozornění + stav FMP limitu.
loadWatchlist();
loadPortfolio();
loadAlerts();
loadFmpStatus();
observeChartCards(); // lazy-load grafů pro všechny karty (analýza, scan, uložené reporty)
loadStoredReports(); // perzistentní analytické reporty (přežívají restart GUI)
input.focus();
