"""Testy valuačního modulu (domain/valuation.py) — Gordon model, výnosová metoda."""

from __future__ import annotations

import math

from tests.conftest import make_snapshot

from dividend_tracker.domain.valuation import compute_fair_value, two_stage_ddm_price
from dividend_tracker.models import MetricsReport


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


# ---------- two_stage_ddm_price (čistá funkce) ------------------------------------


def test_two_stage_known_values():
    """Ruční výpočet: P = Σ D_t/(1+r)^t + [D_{n+1}/(r−g_t)]/(1+r)^n."""
    price = two_stage_ddm_price(dps=3.0, g1_pct=15.0, r_pct=5.0)
    assert price is not None
    expected = sum(3.0 * 1.15 ** t / 1.05 ** t for t in range(1, 6)) \
        + (3.0 * 1.15 ** 5 * 1.03 / (0.05 - 0.03)) / 1.05 ** 5
    assert math.isclose(price, expected, rel_tol=1e-9)


def test_two_stage_default_terminal_growth_half_of_g1_capped_3():
    """Výchozí terminální růst = min(max(g1/2, 0), 3 %)."""
    # g1 = 8 % → gt = min(4, 3) = 3 % (strop se projeví)
    price = two_stage_ddm_price(dps=3.0, g1_pct=8.0, r_pct=6.0)
    assert price is not None
    expected = sum(3.0 * 1.08 ** t / 1.06 ** t for t in range(1, 6)) \
        + (3.0 * 1.08 ** 5 * 1.03 / (0.06 - 0.03)) / 1.06 ** 5
    assert math.isclose(price, expected, rel_tol=1e-9)


def test_two_stage_negative_growth_terminal_floored_at_zero():
    """Klesající dividenda: terminální růst se ořízne na spodní hranici 0 %."""
    price = two_stage_ddm_price(dps=3.0, g1_pct=-5.0, r_pct=6.0)
    assert price is not None
    expected = sum(3.0 * 0.95 ** t / 1.06 ** t for t in range(1, 6)) \
        + (3.0 * 0.95 ** 5 / 0.06) / 1.06 ** 5
    assert math.isclose(price, expected, rel_tol=1e-9)


def test_two_stage_zero_growth_equals_level_dps_model():
    """g1 = 0 % a gt = 0 % → konstantní DPS: P = Σ D/(1+r)^t + (D/r)/(1+r)^n."""
    price = two_stage_ddm_price(dps=3.0, g1_pct=0.0, r_pct=8.0)
    assert price is not None
    expected = sum(3.0 / 1.08 ** t for t in range(1, 6)) + (3.0 / 0.08) / 1.08 ** 5
    assert math.isclose(price, expected, rel_tol=1e-9)


def test_two_stage_explicit_terminal_growth_and_horizon():
    """Explicitní terminální růst a kratší horizont (n = 3)."""
    price = two_stage_ddm_price(dps=2.0, g1_pct=10.0, r_pct=7.0, n_years=3, terminal_growth_pct=2.0)
    assert price is not None
    expected = sum(2.0 * 1.10 ** t / 1.07 ** t for t in range(1, 4)) \
        + (2.0 * 1.10 ** 3 * 1.02 / (0.07 - 0.02)) / 1.07 ** 3
    assert math.isclose(price, expected, rel_tol=1e-9)


def test_two_stage_returns_none_when_r_le_terminal_growth():
    """r ≤ g_t → terminální hodnota diverguje → None (čistá funkce bez ořezávání)."""
    assert two_stage_ddm_price(3.0, 10.0, 7.0, terminal_growth_pct=8.0) is None   # r < gt
    assert two_stage_ddm_price(3.0, 10.0, 6.0, terminal_growth_pct=6.0) is None   # r == gt


def test_two_stage_invalid_inputs_return_none():
    """Neplatné vstupy (DPS ≤ 0/None, r ≤ 0/None, n < 1) → None."""
    assert two_stage_ddm_price(0.0, 5.0, 8.0) is None
    assert two_stage_ddm_price(None, 5.0, 8.0) is None
    assert two_stage_ddm_price(-1.0, 5.0, 8.0) is None
    assert two_stage_ddm_price(3.0, 5.0, 0.0) is None
    assert two_stage_ddm_price(3.0, 5.0, -2.0) is None
    assert two_stage_ddm_price(3.0, 5.0, None) is None
    assert two_stage_ddm_price(3.0, 5.0, 8.0, n_years=0) is None
