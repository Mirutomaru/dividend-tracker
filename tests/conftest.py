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


def make_reit_snapshot(**kw) -> Snapshot:
    """Zdravý REIT profil (styl O/PLD): FFO/AFFO kryjí dividendu, nízká páka.

    Očekávané hodnoty pro testy (přesně odvozené z vstupů):
    - FFO = NI + D&A = 1.2e9 + 1.5e9 = 2.7e9 → ffo_payout = 1.6/2.7 = 59.26 %
    - AFFO = FFO − CapEx = 2.7e9 − 0.5e9 = 2.2e9 → affo_payout = 1.6/2.2 = 72.73 %
    - Net Debt/EBITDA = (3.0 − 0.5)/2.0 = 1.25x; Interest Coverage = 1.6/0.32 = 5.0x
    → REIT safety flag: zelený (cov < 75 %, ND/E < 3.0x, IC > 4.0x).
    FCF je strukturovaně záporné (D&A) — u REITU se netrestá, když existuje FFO/AFFO krytí.
    """
    base = dict(
        ticker="REIT",
        name="Test Realty Trust",
        sector="Real Estate",
        industry="REIT - Residential",
        currency="USD",
        as_of="2026-09-03T12:00:00+00:00",
        price=50.0,
        shares_outstanding=500_000_000,
        market_cap=2.5e10,
        enterprise_value=2.8e10,
        annual_dps={2019: 2.30, 2020: 2.45, 2021: 2.60, 2022: 2.78, 2023: 2.95, 2024: 3.18},
        dividend_payment_counts={y: 4 for y in range(2019, 2025)},
        dps_ttm=3.20,
        forward_dividend_rate=3.36,
        eps_ttm=1.50,          # zkreslené D&A — EPS payout u REITU ignorován
        eps_forward=1.60,
        ebitda=2e9,
        ebit=1.6e9,
        interest_expense=3.2e8,
        net_income=1.2e9,
        depreciation_amortization=1.5e9,   # D&A z CF výkazu (pro FFO)
        capex=5e8,                          # |CapEx| (pro AFFO odhad)
        tax_rate=0.26,
        total_debt=3e9,
        cash_and_equivalents=5e8,
        equity=4e9,
        fcf_ttm=-4e8,          # strukturovaně záporné kvůli D&A — normální pro REIT
        dividends_paid=1.6e9,
        fiscal_year_prices={2021: 42.0, 2022: 44.0, 2023: 46.0, 2024: 48.0},
        pe_history_approx={},
        data_gaps=[],
    )
    base.update(kw)
    return Snapshot(**base)


@pytest.fixture
def healthy_snapshot():
    return make_snapshot()


@pytest.fixture
def reit_snapshot():
    return make_reit_snapshot()
