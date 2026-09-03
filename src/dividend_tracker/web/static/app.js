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
const fmpBar = $("#fmp-bar");
const fmpStatusText = $("#fmp-status-text");
const refreshFmpBtn = $("#refresh-fmp-btn");

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
    const cells = ["ticker", "name", "sector", "price", "change", "volume", "dps_ttm",
      "yield_pct", "growth_5y_pct", "streak_years", "next_ex_date", "next_dividend"]
      .map((k) => `<td class="${/^(price|change|volume|dps_ttm|yield_pct|growth_5y_pct|streak_years|next_dividend)$/.test(k) ? "num" : ""}">${wlCell(k, row)}</td>`)
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
    } else {
      watchlistSection.classList.add("hidden");
      emptyState.classList.remove("hidden");
    }
  } catch {
    /* server nedostupný — zkusit znovu při dalším načtení */
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
    </div>
    ${scoreHtml}
    ${dividendHtml}
    ${valuationHtml}
    ${healthHtml}
    ${fvHtml}
    ${gaps}
  </article>`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ------------------------------ API + polling ------------------------------ */

async function startAnalysis(tickers) {
  setBusy(true);
  resultsEl.innerHTML = "";
  tableBody.innerHTML = "";
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

// Seřazení watchlisty kliknutím na záhlaví (vzestupně/sestupně podle libovolného atributu).
document.querySelectorAll("#watchlist-table thead th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (wlSortKey === key) wlSortDir *= -1;
    else { wlSortKey = key; wlSortDir = typeof wlRows[0]?.[key] === "string" ? 1 : -1; }
    renderWatchlist();
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

// Start: načíst watchlist z DB + stav FMP limitu.
loadWatchlist();
loadFmpStatus();
input.focus();
