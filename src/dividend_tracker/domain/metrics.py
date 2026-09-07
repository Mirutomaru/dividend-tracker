"""Výpočet metriek z :class:`Snapshot` (sekce A/B/C zadání). Čisté funkce, bez sítě."""

from __future__ import annotations

import statistics

from ..models import MetricsReport, Snapshot
from .dividends import cagr, dividend_streak, full_years
from .sectors import is_reit


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    try:
        return a / b
    except (ZeroDivisionError, TypeError):
        return None


def compute_metrics(snap: Snapshot) -> MetricsReport:
    m = MetricsReport(ticker=snap.ticker)

    # ---------- A. Dividendy & udržitelnost -------------------------------------
    dps_ttm = snap.dps_ttm or 0.0
    if snap.price and snap.price > 0:
        m.yield_trailing_pct = round(dps_ttm / snap.price * 100.0, 3)
        fwd_rate = snap.forward_dividend_rate
        fwd = fwd_rate if (fwd_rate is not None and fwd_rate > 0) else dps_ttm
        m.yield_forward_pct = round(fwd / snap.price * 100.0, 3)

    reit = is_reit(snap.sector)

    if reit:
        m.notes.append("REIT: klasický EPS payout ratio ignorován (NI zkreslené D&A nemovitostí) — udržitelnost posuzuje FFO/AFFO payout.")
    elif snap.eps_ttm and snap.eps_ttm > 0:
        m.payout_eps_pct = round(dps_ttm / snap.eps_ttm * 100.0, 2)
    else:
        m.notes.append("Payout ratio (EPS): EPS_TTM k dispozici není nebo ≤ 0.")

    if snap.fcf_ttm is not None and snap.fcf_ttm > 0 and snap.dividends_paid is not None:
        m.fcf_payout_pct = round(snap.dividends_paid / snap.fcf_ttm * 100.0, 2)
    elif snap.fcf_ttm is not None and snap.fcf_ttm <= 0:
        m.notes.append("FCF payout ratio nepočítatelný — FCF ≤ 0 (klíčový signál rizika).")
    else:
        m.notes.append("FCF payout ratio chybí — data dividends_paid nebo FCF nejsou k dispozici.")

    # REIT: FFO ≈ NI + D&A; AFFO ≈ FFO − CapEx (odhad bez rozlišení maintenance/growth capex)
    if reit:
        ffo = None
        if snap.net_income is not None and snap.depreciation_amortization is not None:
            ffo = snap.net_income + snap.depreciation_amortization
        affo = (ffo - snap.capex) if (ffo is not None and snap.capex is not None) else None

        if ffo is not None and ffo > 0 and snap.dividends_paid is not None:
            m.ffo_payout_pct = round(snap.dividends_paid / ffo * 100.0, 2)
        elif ffo is not None and ffo <= 0:
            m.notes.append("FFO ≤ 0 — dividendy nejsou pokryty FFO (výrazné riziko škrtení).")
        else:
            m.notes.append("FFO payout chybí — není k dispozici NI nebo D&A.")

        if affo is not None and affo > 0 and snap.dividends_paid is not None:
            m.affo_payout_pct = round(snap.dividends_paid / affo * 100.0, 2)
            m.notes.append("AFFO odhadnuto jako FFO − CapEx (bez rozlišení maintenance/growth capex).")
        elif affo is not None and affo <= 0:
            m.notes.append("Odhad AFFO ≤ 0 — dividendy přesahují hotovostní kapacitu po investicích.")

    fy = full_years(snap.annual_dps, snap.dividend_payment_counts)
    if fy != {int(k): v for k, v in snap.annual_dps.items()}:
        m.notes.append("Nejnovější (neúplný) fiskální rok vynechán ze streaku/CAGR.")
    m.cagr_3y_pct = cagr(fy, 3)
    m.cagr_5y_pct = cagr(fy, 5)
    m.cagr_10y_pct = cagr(fy, 10)
    m.streak_years = dividend_streak(fy)

    # ---------- B. Ocenění & valuace ---------------------------------------------
    if snap.eps_ttm and snap.eps_ttm > 0 and snap.price:
        m.pe_ttm = round(snap.price / snap.eps_ttm, 2)
    elif snap.pe_ttm_reported is not None and snap.pe_ttm_reported > 0:
        m.pe_ttm = round(float(snap.pe_ttm_reported), 2)
    if snap.eps_forward and snap.eps_forward > 0 and snap.price:
        m.pe_forward = round(snap.price / snap.eps_forward, 2)
    elif snap.pe_forward_reported is not None and snap.pe_forward_reported > 0:
        m.pe_forward = round(float(snap.pe_forward_reported), 2)

    fcf_ps = _safe_div(snap.fcf_ttm, snap.shares_outstanding) if snap.fcf_ttm else None
    if fcf_ps and fcf_ps > 0 and snap.price:
        m.p_fcf = round(snap.price / fcf_ps, 2)
        m.fcf_yield_pct = round(fcf_ps / snap.price * 100.0, 3)

    if snap.enterprise_value and snap.ebitda and snap.ebitda > 0:
        m.ev_ebitda = round(snap.enterprise_value / snap.ebitda, 2)
    elif snap.ev_ebitda_reported is not None and snap.ev_ebitda_reported > 0:
        m.ev_ebitda = round(float(snap.ev_ebitda_reported), 2)

    # P/B a Graham číslo — potřebují účetní hodnotu na akcii (equity ÷ shares).
    bvps = _safe_div(snap.equity, snap.shares_outstanding) if snap.equity else None
    if bvps and bvps > 0 and snap.price:
        m.p_b = round(snap.price / bvps, 2)
    # Graham číslo = √(22,5 × EPS_TTM × BVPS); 22,5 = P/E 15 × P/B 1,5 (Graham & Dodd).
    if snap.eps_ttm and snap.eps_ttm > 0 and bvps and bvps > 0:
        m.graham_number = round((22.5 * snap.eps_ttm * bvps) ** 0.5, 2)
    # Peter Lynch PEG = P/E ÷ růst (%). Růst = 5letý CAGR dividendy (proxy —
    # historie EPS není v DB); <1 levný vůči růstu, >2 drahý.
    if m.pe_ttm and m.cagr_5y_pct is not None and m.cagr_5y_pct > 0:
        m.lynch_peg = round(m.pe_ttm / m.cagr_5y_pct, 2)

    # 5letý průměr dividendového výnosu: yield_y = DPS_y / závěrečná cena_y (max. 5 plných let)
    yields_5y = []
    for y in sorted(set(snap.annual_dps) & set(snap.fiscal_year_prices))[-5:]:
        p_end, d = snap.fiscal_year_prices[y], snap.annual_dps[y]
        if p_end and p_end > 0 and d and d > 0:
            yields_5y.append(d / p_end * 100.0)
    if len(yields_5y) >= 2:
        m.yield_5y_avg_pct = round(statistics.mean(yields_5y), 3)
        trailing = m.yield_trailing_pct
        if trailing is not None and trailing > 0 and m.yield_5y_avg_pct > 0:
            m.yield_vs_5y_deviation_pct = round(
                (trailing / m.yield_5y_avg_pct - 1.0) * 100.0, 2
            )
    else:
        m.notes.append("5Y avg yield: méně než 2 roky historických dat — srovnání není možné.")

    # ---------- C. Finanční zdraví & solvence ------------------------------------
    net_debt = None
    if snap.total_debt is not None and snap.cash_and_equivalents is not None:
        net_debt = max(snap.total_debt - snap.cash_and_equivalents, 0.0)
    if net_debt is not None and snap.ebitda and snap.ebitda > 0:
        m.net_debt_to_ebitda = round(net_debt / snap.ebitda, 2)

    if snap.ebit is not None and snap.interest_expense and snap.interest_expense > 0:
        m.interest_coverage = round(snap.ebit / snap.interest_expense, 2)
    elif snap.interest_expense in (None, 0):
        m.notes.append("Interest coverage: úrokové výdaje nejsou k dispozici.")

    # ROIC = NOPAT / Invested Capital; NOPAT ≈ EBIT * (1 - tax_rate), IC ≈ equity + debt − cash
    if snap.ebit is not None and snap.equity is not None:
        tax = snap.tax_rate if snap.tax_rate is not None else 0.25
        nopat = snap.ebit * (1.0 - tax)
        ic = snap.equity + (snap.total_debt or 0.0) - (snap.cash_and_equivalents or 0.0)
        if ic > 0:
            m.roic_pct = round(nopat / ic * 100.0, 2)

    # přenést gaps ze snapshotu do poznámek (scoring váhy upravi podle dostupnosti)
    for g in snap.data_gaps:
        m.notes.append(f"data_gap: {g}")
    return m
