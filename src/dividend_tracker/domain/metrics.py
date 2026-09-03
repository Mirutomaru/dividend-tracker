"""Výpočet metriek z :class:`Snapshot` (sekce A/B/C zadání). Čisté funkce, bez sítě."""

from __future__ import annotations

import statistics
from typing import Optional

from ..models import MetricsReport, Snapshot
from .dividends import cagr, dividend_streak, full_years


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
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
        fwd = snap.forward_dividend_rate if (snap.forward_dividend_rate or 0) > 0 else dps_ttm
        m.yield_forward_pct = round(fwd / snap.price * 100.0, 3)

    if snap.eps_ttm and snap.eps_ttm > 0:
        m.payout_eps_pct = round(dps_ttm / snap.eps_ttm * 100.0, 2)
    else:
        m.notes.append("Payout ratio (EPS): EPS_TTM k dispozici není nebo ≤ 0.")

    if snap.fcf_ttm is not None and snap.fcf_ttm > 0 and snap.dividends_paid is not None:
        m.fcf_payout_pct = round(snap.dividends_paid / snap.fcf_ttm * 100.0, 2)
    elif snap.fcf_ttm is not None and snap.fcf_ttm <= 0:
        m.notes.append("FCF payout ratio nepočítatelný — FCF ≤ 0 (klíčový signál rizika).")
    else:
        m.notes.append("FCF payout ratio chybí — data dividends_paid nebo FCF nejsou k dispozici.")

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
    elif (snap.pe_ttm_reported or 0) > 0:
        m.pe_ttm = round(float(snap.pe_ttm_reported), 2)
    if snap.eps_forward and snap.eps_forward > 0 and snap.price:
        m.pe_forward = round(snap.price / snap.eps_forward, 2)
    elif (snap.pe_forward_reported or 0) > 0:
        m.pe_forward = round(float(snap.pe_forward_reported), 2)

    fcf_ps = _safe_div(snap.fcf_ttm, snap.shares_outstanding) if snap.fcf_ttm else None
    if fcf_ps and fcf_ps > 0 and snap.price:
        m.p_fcf = round(snap.price / fcf_ps, 2)
        m.fcf_yield_pct = round(fcf_ps / snap.price * 100.0, 3)

    if snap.enterprise_value and snap.ebitda and snap.ebitda > 0:
        m.ev_ebitda = round(snap.enterprise_value / snap.ebitda, 2)
    elif (snap.ev_ebitda_reported or 0) > 0:
        m.ev_ebitda = round(float(snap.ev_ebitda_reported), 2)

    # 5letý průměr dividendového výnosu: yield_y = DPS_y / závěrečná cena_y (max. 5 plných let)
    yields_5y = []
    for y in sorted(set(snap.annual_dps) & set(snap.fiscal_year_prices))[-5:]:
        p_end, d = snap.fiscal_year_prices[y], snap.annual_dps[y]
        if p_end and p_end > 0 and d and d > 0:
            yields_5y.append(d / p_end * 100.0)
    if len(yields_5y) >= 2:
        m.yield_5y_avg_pct = round(statistics.mean(yields_5y), 3)
        if (m.yield_trailing_pct or 0) > 0 and m.yield_5y_avg_pct > 0:
            m.yield_vs_5y_deviation_pct = round(
                (m.yield_trailing_pct / m.yield_5y_avg_pct - 1.0) * 100.0, 2
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
