"""Společné fixture — syntetický snapshot zdravé dividendové firmy (bez sítě)."""

from __future__ import annotations

import pytest

from dividend_tracker.models import Benchmark, Snapshot


def make_snapshot(**kw) -> Snapshot:
    """Základní 'zdravý' profil (styl KO/JNJ): solidní payout, nízký dluh, rostoucí dividendy."""
    base = dict(
        ticker="TEST",
        name="Test Corp",
        sector="Consumer Defensive",
        industry="Beverages",
        currency="USD",
        as_of="2026-09-03T12:00:00+00:00",
        price=60.0,
        shares_outstanding=1_000_000_000,
        market_cap=5e10,
        enterprise_value=5.5e10,
        annual_dps={2020: 3.00, 2021: 3.18, 2022: 3.37, 2023: 3.56, 2024: 3.76, 2025: 3.96},
        dividend_payment_counts={y: 4 for y in range(2020, 2026)},
        dps_ttm=3.96,
        forward_dividend_rate=4.14,
        eps_ttm=12.0,
        eps_forward=12.8,
        ebitda=15e9,
        ebit=13e9,
        interest_expense=1e9,
        net_income=10e9,
        tax_rate=0.24,
        total_debt=6e9,
        cash_and_equivalents=3e9,
        equity=30e9,
        fcf_ttm=8e9,
        dividends_paid=4e9,
        fiscal_year_prices={2021: 45.0, 2022: 47.0, 2023: 48.0, 2024: 49.0, 2025: 50.0},
        pe_history_approx={},
        data_gaps=[],
    )
    base.update(kw)
    return Snapshot(**base)


def make_benchmark() -> Benchmark:
    return Benchmark(sector="Consumer Defensive", median_pe_ttm=24.0, median_ev_ebitda=12.0, n_peers=8)


@pytest.fixture
def healthy_snapshot():
    return make_snapshot()
