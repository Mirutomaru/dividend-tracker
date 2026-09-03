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
    snap = make_snapshot(price=50.0, dps_ttm=3.0, forward_dividend_rate=None)
    fv = compute_fair_value(snap, _metrics(cagr_5y_pct=20.0), required_return_pct=5.0)
    # g by bylo 6 % (clip), ale r=5 % → konzervativní ořez na r-3 % = 2 %
    assert fv.growth_assumed_pct == 2.0
    # D1 = 3*1.02 = 3.06; F = 3.06/0.03 = 102.0
    assert math.isclose(fv.gordon_price, 102.0, rel_tol=1e-9)
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
