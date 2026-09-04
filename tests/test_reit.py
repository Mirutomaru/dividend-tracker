"""Testy REIT logiky (domain/metrics.py, domain/scoring.py).

Pokrývají: výpočet FFO/AFFO payout v compute_metrics a REIT safety flag
v compute_safety_flag (zelená/žlutá/červená) — včetně toho, že strukturovaně
záporné FCF u REITU se netrestá, když existuje FFO/AFFO krytí.

Vstupní fixture: make_reit_snapshot() z conftest.py (zdravý profil → green).
"""

from __future__ import annotations

import pytest

from dividend_tracker.domain.metrics import compute_metrics
from dividend_tracker.domain.scoring import SIGNAL_DIVIDEND_TRAP, compute_safety_flag, compute_score

# ---------- A. Výpočet FFO/AFFO v compute_metrics -------------------------------


def test_ffo_affo_payout_known_values(reit_snapshot):
    """FFO = NI + D&A; AFFO = FFO − CapEx; payouty = dividends_paid / metrika."""
    m = compute_metrics(reit_snapshot)
    # FFO = 1.2e9 + 1.5e9 = 2.7e9 → 1.6/2.7 = 59.26 %
    assert m.ffo_payout_pct == pytest.approx(59.26, abs=0.01)
    # AFFO = 2.7e9 − 0.5e9 = 2.2e9 → 1.6/2.2 = 72.73 %
    assert m.affo_payout_pct == pytest.approx(72.73, abs=0.01)


def test_reit_ignores_eps_payout(reit_snapshot):
    """U REITU se EPS payout ratio nepočítá (NI zkreslené D&A nemovitostí)."""
    m = compute_metrics(reit_snapshot)
    assert m.payout_eps_pct is None
    assert any("FFO/AFFO" in n for n in m.notes)


def test_non_reit_has_no_ffo_fields(healthy_snapshot):
    """Klasická firma: FFO/AFFO pole zůstávají None (logika jen pro REIT)."""
    m = compute_metrics(healthy_snapshot)
    assert m.ffo_payout_pct is None
    assert m.affo_payout_pct is None


def test_ffo_missing_when_da_missing(reit_snapshot):
    """Bez D&A se FFO nepočítá — pole None + poznámka o chybějících datech."""
    s = reit_snapshot.model_copy(update={"depreciation_amortization": None})
    m = compute_metrics(s)
    assert m.ffo_payout_pct is None
    assert m.affo_payout_pct is None  # AFFO závisí na FFO
    assert any("FFO payout chybí" in n for n in m.notes)


def test_ffo_negative_flagged(reit_snapshot):
    """FFO ≤ 0 → dividendy nekryté (výrazné riziko škrtení), poznámka v notes."""
    s = reit_snapshot.model_copy(update={"net_income": -2e9})  # FFO = −2 + 1.5 = −0.5e9
    m = compute_metrics(s)
    assert m.ffo_payout_pct is None
    assert any("FFO ≤ 0" in n for n in m.notes)


def test_affo_negative_flagged(reit_snapshot):
    """AFFO ≤ 0 (CapEx > FFO) → dividendy přesahují hotovostní kapacitu."""
    s = reit_snapshot.model_copy(update={"capex": 3e9})  # AFFO = 2.7 − 3.0 = −0.3e9
    m = compute_metrics(s)
    assert m.ffo_payout_pct == pytest.approx(59.26, abs=0.01)  # FFO zůstává kladné
    assert m.affo_payout_pct is None
    assert any("AFFO ≤ 0" in n for n in m.notes)


def test_affo_missing_when_capex_missing(reit_snapshot):
    """Bez CapEx se AFFO nepočítá, FFO payout zůstává dostupné."""
    s = reit_snapshot.model_copy(update={"capex": None})
    m = compute_metrics(s)
    assert m.ffo_payout_pct == pytest.approx(59.26, abs=0.01)
    assert m.affo_payout_pct is None


# ---------- B. REIT safety flag v compute_safety_flag ----------------------------


def test_reit_green_flag(reit_snapshot):
    """Zdravý REIT: cov 72.73 % < 75 %, ND/E 1.25x < 3.0, IC 5.0 > 4 → zelená."""
    m = compute_metrics(reit_snapshot)
    flag, reasons = compute_safety_flag(reit_snapshot, m)
    assert flag == "green"
    assert any("FFO/AFFO payout" in r for r in reasons)


def test_reit_red_when_affo_payout_over_100(reit_snapshot):
    """AFFO payout > 100 % → červená (dividendy překračují hotovostní krytí)."""
    s = reit_snapshot.model_copy(update={"dividends_paid": 3e9})  # AFFO payout 136.4 %
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "red"
    assert any("AFFO payout ratio" in r and "> 100 %" in r for r in reasons)


def test_reit_red_when_ffo_payout_over_100(reit_snapshot):
    """FFO payout > 100 % (bez AFFO dat) → červená, label FFO."""
    s = reit_snapshot.model_copy(
        update={"dividends_paid": 3e9, "capex": None}
    )  # FFO payout 111.1 %, AFFO nepočítatelné
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "red"
    assert any("FFO payout ratio" in r for r in reasons)


def test_reit_red_when_net_debt_too_high(reit_snapshot):
    """Net Debt/EBITDA > 4.5x → červená i při dobrém FFO krytí."""
    s = reit_snapshot.model_copy(update={"total_debt": 12e9})  # ND/E = (12−0.5)/2 = 5.75x
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "red"
    assert any("Net Debt/EBITDA" in r for r in reasons)


def test_reit_yellow_when_coverage_between_75_and_100(reit_snapshot):
    """Cov 90.9 % (mezi 75 a 100 %) → žlutá: nekrytí, ale ani červený breach."""
    s = reit_snapshot.model_copy(update={"dividends_paid": 2e9})  # AFFO payout 90.9 %
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "yellow"


def test_reit_yellow_when_coverage_data_missing(reit_snapshot):
    """Bez FFO/AFFO dat (chybí NI i D&A) a kladným FCF → žlutá (zelená neprokázatelná)."""
    s = reit_snapshot.model_copy(
        update={"net_income": None, "depreciation_amortization": None, "fcf_ttm": 1e9}
    )
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "yellow"
    assert any("FFO/AFFO data" in r for r in reasons)


def test_reit_negative_fcf_not_red_when_ffo_covers(reit_snapshot):
    """Klíčové REIT pravidlo: záporné FCF se netrestá, když existuje FFO/AFFO krytí."""
    m = compute_metrics(reit_snapshot)  # fcf_ttm = −4e8 (strukturovaně), cov 72.73 %
    flag, _ = compute_safety_flag(reit_snapshot, m)
    assert flag == "green"


def test_reit_negative_fcf_red_when_no_ffo_data(reit_snapshot):
    """Záporné FCF + chybějící FFO/AFFO → červená (krytí neprokázáno)."""
    s = reit_snapshot.model_copy(
        update={"net_income": None, "depreciation_amortization": None}
    )  # fcf_ttm zůstává −4e8
    m = compute_metrics(s)
    flag, reasons = compute_safety_flag(s, m)
    assert flag == "red"
    assert any("FFO/AFFO data chybí" in r for r in reasons)


# ---------- C. REIT v celkovém skóre (compute_score) -----------------------------


def test_reit_dividend_trap_signal_and_cap(reit_snapshot):
    """Yield > 8 % + AFFO payout > 100 % → dividend trap, skóre omezeno na ≤25."""
    s = reit_snapshot.model_copy(update={"price": 35.0, "dividends_paid": 3e9})
    m = compute_metrics(s)  # trailing yield 3.2/35 = 9.14 % > 8 %
    assert (m.yield_trailing_pct or 0) > 8.0
    score = compute_score(s, m)
    assert SIGNAL_DIVIDEND_TRAP in score.signals
    assert score.safety_flag == "red"
    assert score.total is not None and score.total <= 25.0


def test_reit_red_flag_caps_total_at_39(reit_snapshot):
    """Červený flag (AFFO payout > 100 %) omezuje skóre na ≤39."""
    s = reit_snapshot.model_copy(update={"dividends_paid": 3e9})
    m = compute_metrics(s)
    score = compute_score(s, m)
    assert score.safety_flag == "red"
    assert score.total is not None and score.total <= 39.0
