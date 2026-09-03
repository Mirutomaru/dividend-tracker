/* ============================================================
   Dividend Tracker — frontend logika (vanilla JS, žádné závislosti)
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

let pollTimer = null;

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

/* ------------------------------ renderování ------------------------------ */

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
  const bd = score.breakdown || {};

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

  return `<article class="card ticker-card">
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

      // Render hotové položky postupně.
      for (const entry of job.results) {
        const el = document.getElementById(`card-${entry.ticker}`);
        if (!el) {
          resultsEl.insertAdjacentHTML("beforeend", `<div id="card-${entry.ticker}">${renderTickerCard(entry)}</div>`);
          animateRing(document.querySelector(`#card-${entry.ticker} .ring-fg`));
        }
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
  const tickers = input.value.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean);
  if (!tickers.length) return;
  startAnalysis(tickers.slice(0, 10));
});

input.focus();
