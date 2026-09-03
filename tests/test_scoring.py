"""Testy scoring modelu (domain/scoring.py) — flagy, signály, omezení skóre."""

from __future__ import annotations

import pytest

from dividend_tracker.domain.metrics import compute_metrics
from dividend_tracker.domain.scoring import (
    SIGNAL_BUY_ZONE,
    SIGNAL_DIVIDEND_TRAP,
    SIGNAL_NO_DIVIDEND,
    compute_score,
)
from tests.conftest import make_benchmark, make_snapshot


def test_healthy_green_flag_high_score(healthy_snapshot):
    m = compute_metrics(healthy_snapshot)
    score = compute_score(healthy_snapshot, m, make_benchmark())
    assert score.applicable is True
    assert score.safety_flag == "green"
    assert score.total is not None and 70 < score.total <= 100
    assert SIGNAL_DIVIDEND_TRAP not in score.signals
    assert SIGNAL_BUY_ZONE not in score.signals


def test_red_flag_on_negative_fcf(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"fcf_ttm": -2e9})
    m = compute_metrics(s)
    score = compute_score(s, m, make_benchmark())
    assert score.safety_flag == "red"
    assert score.total is not None and score.total <= 39.0
    # yield < 8 % → trap signál se nespouští (i když FCF je záporné)
    assert SIGNAL_DIVIDEND_TRAP not in score.signals


def test_dividend_trap_signal_and_cap(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"price": 45.0, "fcf_ttm": -2e9})
    m = compute_metrics(s)  # trailing yield 8,8 % + záporné FCF → trap
    assert (m.yield_trailing_pct or 0) > 8.0
    score = compute_score(s, m, make_benchmark())
    assert SIGNAL_DIVIDEND_TRAP in score.signals
    assert score.safety_flag == "red"
    assert score.total is not None and score.total <= 25.0


def test_buy_zone_signal_when_yield_above_5y_avg(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"price": 45.0})
    m = compute_metrics(s)  # yield 8,8 % > +15 % nad 5Y průměrem
    assert (m.yield_vs_5y_deviation_pct or -999) > 15.0
    score = compute_score(s, m, make_benchmark())
    assert score.safety_flag == "green"
    assert SIGNAL_BUY_ZONE in score.signals
    assert score.total is not None and score.total > 60


def test_no_dividend_not_applicable(healthy_snapshot):
    s = healthy_snapshot.model_copy(update={"dps_ttm": 0.0, "annual_dps": {}, "dividend_payment_counts": {}})
    m = compute_metrics(s)
    score = compute_score(s, m, make_benchmark())
    assert score.applicable is False
    assert score.total is None
    assert score.signals == [SIGNAL_NO_DIVIDEND]


def test_missing_data_still_scores(healthy_snapshot):
    s = healthy_snapshot.model_copy(
        update={
            "ebitda": None, "ebit": None, "interest_expense": None, "net_income": None,
            "tax_rate": None, "total_debt": None, "cash_and_equivalents": None,
            "equity": None, "fcf_ttm": None, "dividends_paid": None, "eps_forward": None,
        }
    )
    m = compute_metrics(s)
    score = compute_score(s, m, make_benchmark())  # bez benchmarku i s ním
    assert score.applicable is True
    assert score.total is not None and 0 <= score.total <= 100
