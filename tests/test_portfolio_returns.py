"""Offline testy výkonnostních metrik portfolia — TWR a IRR (domain/portfolio_returns.py).

Čisté funkce bez sítě i DB. Konvence cash flow: buy = záporné, sell/dividend = kladné.
"""

from __future__ import annotations

import pytest

from dividend_tracker.domain.portfolio_returns import (
    CashFlow,
    internal_rate_of_return,
    time_weighted_return,
)

# ------------------------------ TWR --------------------------------------------

def test_twr_simple_growth():
    """Jen růst hodnoty bez externích toků → TWR = změna hodnoty v %."""
    assert time_weighted_return([("2024-01-01", 100.0), ("2025-01-01", 110.0)], {}) == pytest.approx(10.0, abs=0.01)


def test_twr_with_dividend_outflow():
    """Dividenda (kladný externí tok) se odečte od hodnoty před výpočtem růstu."""
    # V(t1)=108 po vyplacení 8; hodnota PŘED dividendou = 108 + 8 = 116 → růst 16 %.
    assert time_weighted_return(
        [("2024-01-01", 100.0), ("2025-01-01", 108.0)], {"2025-01-01": 8.0}
    ) == pytest.approx(16.0, abs=0.01)


def test_twr_multi_period_chain_linking():
    """Více období se řetězí: TWR = Π(1+r_k) − 1."""
    # Období 1: 100 → 120 (+20 %), období 2: 120 → 96 (−20 %) → TWR = 1.2*0.8 − 1 = −4 %.
    assert time_weighted_return(
        [("2023-01-01", 100.0), ("2024-01-01", 120.0), ("2025-01-01", 96.0)], {}
    ) == pytest.approx(-4.0, abs=0.01)


def test_twr_insufficient_data():
    assert time_weighted_return([], {}) is None
    assert time_weighted_return([("2024-01-01", 100.0)], {}) is None


# ------------------------------ IRR --------------------------------------------

def test_irr_one_year_gain():
    """Nákup −100, za rok hodnota 110 → IRR ≈ +10 % p.a."""
    assert internal_rate_of_return(
        [CashFlow("2024-01-01", -100.0)], 110.0, "2025-01-01"
    ) == pytest.approx(10.0, abs=0.05)


def test_irr_two_year_compound():
    """Nákup −100, za 2 roky hodnota 121 → (1+r)^2 = 1.21 → r ≈ +10 % p.a."""
    assert internal_rate_of_return(
        [CashFlow("2023-01-01", -100.0)], 121.0, "2025-01-01"
    ) == pytest.approx(10.0, abs=0.05)


def test_irr_negative_return():
    """Nákup −100, za rok hodnota 90 → IRR ≈ −10 % p.a."""
    assert internal_rate_of_return(
        [CashFlow("2024-01-01", -100.0)], 90.0, "2025-01-01"
    ) == pytest.approx(-10.0, abs=0.05)


def test_irr_insufficient_data():
    assert internal_rate_of_return([], 100.0, "2025-01-01") is None
    assert internal_rate_of_return([CashFlow("2024-01-01", -100.0)], None, "2025-01-01") is None
