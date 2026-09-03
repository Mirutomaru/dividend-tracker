"""Testy valuačního modulu (domain/valuation.py) — Gordon model, výnosová metoda."""

from __future__ import annotations

import math

from dividend_tracker.domain.valuation import compute_fair_value
from dividend_tracker.models import MetricsReport, Snapshot
from tests.conftest import make_snapshot


def _metrics(**kw) -> MetricsReport:
    base = dict(ticker="T", cagr_5y_pct=4.0, yield_trailing_pct=6.0, yield_5y_avg_pct=2.5)
    base.update(kw)
    return MetricsReport(**base)


def test_gordon_and_yield_based_known_values():
    snap = make_snapshot(price=50.0, dps_ttm=3.0, forward_dividend_rate=None)
    fv = compute_fair_value(snap, _metrics(), required_return_pct=8.0)
    # Gordon: D1 = 3*1.04 = 3.12; F = 3.12/(0.08-0.04) = 78.0
    assert math.isclose(fv.gordon_price, 78.0, rel_tol=1e-9)
    # Výnosová: P = 3.0/0.025 = 120.0 (forward DPS nedostupné → DPS_TTM)
    assert math.isclose(fv.yield_based_price, 120.0, rel_tol=1e-9)
    # Férová cena = průměr; nákupní zóna [0.85*FV, FV]
    assert math.isclose(fv.fair_value, 99.0, rel_tol=1e-9)
    assert fv.buy_zone_high == 99.0 and math.isclose(fv.buy_zone_low, 84.15, rel_tol=1e-6)
    # cena 50 < 90 % FV → podhodnoceno
    assert fv.verdict == "undervalued"


def test_growth_guard_when_g_ge_r():
    """CAGR 5Y ≥ požadovaná výkonnost → Gordon diverguje, použije se two-stage DDM."""
    snap = make_snapshot(price=50.0, dps_ttm=3.0, forward_dividend_rate=None)
    fv = compute_fair_value(snap, _metrics(cagr_5y_pct=20.0), required_return_pct=5.0)
    # CAGR 20 % oříznutý na [0 %, +15 %] → g1 = 15 %; terminální růst min(7.5, 3) = 3 %
    assert fv.ddm_model == "two_stage"
    assert fv.growth_assumed_pct == 15.0
    assert fv.gordon_price is None
    # P = Σ_{t=1..5} D_t/(1+r)^t + TV/(1+r)^5, kde TV = D_6/(r − g_t) — ruční výpočet
    expected = sum(3.0 * 1.15 ** t / 1.05 ** t for t in range(1, 6)) \
        + (3.0 * 1.15 ** 5 * 1.03 / (0.05 - 0.03)) / 1.05 ** 5
    assert fv.two_stage_price is not None
    assert math.isclose(fv.two_stage_price, round(expected, 2), rel_tol=1e-9)


def test_two_stage_fallback_to_gordon_when_not_converging():
    """Two-stage nekonverguje (r ≤ terminální růst) → konzervativní ořez Gordonu."""
    snap = make_snapshot(price=50.0, dps_ttm=3.0, forward_dividend_rate=None)
    fv = compute_fair_value(snap, _metrics(cagr_5y_pct=20.0), required_return_pct=2.0)
    # terminální růst 3 % > r = 2 % → two-stage None; Gordon: g clip [−2 %, +6 %] = 6 %,
    # pak g ≥ r → konzervativní ořez na max(0, r − 3 %) = 0 %
    assert fv.ddm_model == "gordon"
    assert fv.two_stage_price is None
    assert fv.growth_assumed_pct == 0.0
    # D1 = 3*1.00 = 3.0; F = 3.0/0.02 = 150.0
    assert fv.gordon_price is not None
    assert math.isclose(fv.gordon_price, 150.0, rel_tol=1e-9)
    assert any("konzervativní ořez" in n for n in fv.notes)


def test_overvalued_verdict():
    snap = make_snapshot(price=50.0, dps_ttm=3.0, forward_dividend_rate=None)
    fv = compute_fair_value(snap, _metrics(yield_5y_avg_pct=12.0), required_return_pct=8.0)
    # výnosová cena 3/0.12 = 25; FV ≈ (78+25)/2 = 51.5 → cena 50 je férová…
    assert fv.verdict in {"fair", "undervalued"}


def test_no_dividend_returns_unknown():
    snap = make_snapshot(price=50.0, dps_ttm=0.0)
    fv = compute_fair_value(snap, _metrics(), required_return_pct=8.0)
    assert fv.verdict == "unknown"
    assert fv.gordon_price is None and fv.fair_value is None
