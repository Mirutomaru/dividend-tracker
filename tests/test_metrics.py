"""Testy výpočtu metrik (domain/metrics.py)."""

from __future__ import annotations

import math

from dividend_tracker.domain.metrics import compute_metrics
from dividend_tracker.domain.dividends import aggregate_dividends, cagr, dividend_streak, full_years
from tests.conftest import make_snapshot


def test_yields_and_payouts(healthy_snapshot):
    m = compute_metrics(healthy_snapshot)
    # 3.96 / 60 * 100
    assert math.isclose(m.yield_trailing_pct, round(3.96 / 60 * 100, 3), rel_tol=1e-9)
    # payout EPS = 3.96/12*100 = 33 %
    assert m.payout_eps_pct == 33.0
    # FCF payout = 4e9/8e9 = 50 %
    assert m.fcf_payout_pct == 50.0


def test_cagr_and_streak(healthy_snapshot):
    m = compute_metrics(healthy_snapshot)
    expected_5y = round(((3.96 / 3.00) ** (1 / 5) - 1) * 100, 2)
    assert math.isclose(m.cagr_5y_pct, expected_5y, rel_tol=1e-9)
    # ruku po ruce rostoucí dividendy 2020→2025 = 5 let růstu (každý rok > předchozí)
    assert m.streak_years == 5


def test_health_ratios(healthy_snapshot):
    m = compute_metrics(healthy_snapshot)
    # Net Debt/EBITDA = (6-3)e9 / 15e9 = 0.2x
    assert math.isclose(m.net_debt_to_ebitda, 0.2, rel_tol=1e-9)
    # Interest Coverage = 13e9/1e9 = 13x
    assert m.interest_coverage == 13.0
    # ROIC ≈ NOPAT/IC = (13*0.76)e9 / 33e9 ≈ 29.94 %
    expected_roic = round(13e9 * (1 - 0.24) / ((30 + 6 - 3) * 1e9) * 100, 2)
    assert math.isclose(m.roic_pct, expected_roic, rel_tol=1e-9)


def test_yield_vs_5y_avg(healthy_snapshot):
    m = compute_metrics(healthy_snapshot)
    # aktuální cena (60) > year-end 2025 (50) → aktuální yield pod historickým průměrem
    assert m.yield_5y_avg_pct is not None and m.yield_5y_avg_pct > 7.0
    assert m.yield_vs_5y_deviation_pct is not None and m.yield_vs_5y_deviation_pct < 0


def test_negative_fcf_note(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"fcf_ttm": -2e9})
    m = compute_metrics(s)
    assert m.fcf_payout_pct is None
    assert any("FCF ≤ 0" in n for n in m.notes)


def test_missing_interest_expense(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"interest_expense": None})
    m = compute_metrics(s)
    assert m.interest_coverage is None
    assert any("Interest coverage" in n for n in m.notes)


# ------------------------------ dividendní helpery --------------------------------

def test_aggregate_dividends_ttm():
    import pandas as pd

    idx = pd.to_datetime(["2025-11-30", "2026-02-30".replace("-30", "-27"), "2026-05-29"])
    s = pd.Series([1.0, 1.0, 1.0], index=idx)
    annual, counts, ttm = aggregate_dividends(s)
    assert set(annual) == {2025, 2026}
    assert math.isclose(ttm, 3.0, rel_tol=1e-9)  # vše v TTM okně od poslední výplaty


def test_cagr_insufficient_data():
    assert cagr({2024: 1.0}, 5) is None
    assert cagr({}, 5) is None


def test_full_years_excludes_partial_latest():
    annual = {2023: 4.0, 2024: 4.2, 2025: 4.4}
    counts = {2023: 4, 2024: 4, 2025: 1}  # 2025 jen jedna výplata → neúplný rok
    fy = full_years(annual, counts)
    assert 2025 not in fy and 2023 in fy


def test_streak_breaks_on_decline():
    annual = {2021: 4.0, 2022: 4.2, 2023: 4.5, 2024: 4.3}  # pokles v 2024 → streak přerušen
    assert dividend_streak(annual) == 0


def test_streak_counts_consecutive_growth():
    annual = {2021: 4.0, 2022: 4.2, 2023: 4.5}
    assert dividend_streak(annual) == 2
