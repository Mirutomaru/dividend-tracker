"""Doménové datové modely — normalizovaný snapshot a výsledky analýzy (Pydantic v2).

Konvence jednotek:
- pole s příponou ``_pct`` obsahují číselné procenta (23.4 == 23,4 %),
- poměrová pole (např. ``net_debt_to_ebitda``) jsou bezrozměrné násobky (1.5 == 1,5x).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    """Všechna surová data o jedné společnosti na jednom místě — hranice datové vrstvy.

    Neznámé hodnoty jsou ``None`` a doplní se do :attr:`data_gaps`, aby scoring
    mohl váhu chybějících položek upravit místo tichého zero.
    """

    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str = "USD"
    as_of: str | None = None  # ISO timestamp sběru dat

    price: float | None = None
    shares_outstanding: int | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None

    # Dividendy (za fiskální rok; fiskální rok ≈ kalendářní rok, viz domain/dividends.py)
    annual_dps: dict[int, float] = Field(default_factory=dict)
    dividend_payment_counts: dict[int, int] = Field(default_factory=dict)
    dps_ttm: float | None = None  # dividenda na akcii TTM (posledních 365 dnů od poslední výplaty)
    forward_dividend_rate: float | None = None  # očekávaná roční DPS (forward)

    # Výsledek / vstupy valuace (nejnovější fiskální rok nebo TTM, dle providera)
    eps_ttm: float | None = None
    eps_forward: float | None = None
    ebitda: float | None = None
    ebit: float | None = None
    interest_expense: float | None = None
    net_income: float | None = None
    tax_rate: float | None = None  # 0.25 == 25 %

    # Bilance / cash flow (nejnovější fiskální rok)
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    equity: float | None = None
    fcf_ttm: float | None = None  # volný hotovostní tok (roční, nejnovější k dispozici)
    dividends_paid: float | None = None  # reálně vyplacené dividendy z CF výkazu
    depreciation_amortization: float | None = None  # D&A z CF výkazu (pro FFO/AFFO u REIT)
    capex: float | None = None  # kapitálové výdaje, kladná velikost |CapEx| (AFFO odhad)

    # Násobky hlášené direktně providerem (fallback / cross-check)
    pe_ttm_reported: float | None = None
    pe_forward_reported: float | None = None
    ev_ebitda_reported: float | None = None

    # Historie pro kontext yieldu a P/E (fiskální rok -> hodnota)
    fiscal_year_prices: dict[int, float] = Field(default_factory=dict)  # závěrečná cena za rok
    pe_history_approx: dict[int, float] = Field(default_factory=dict)   # P/E aprox. (konstantní EPS_TTM!)

    data_gaps: list[str] = Field(default_factory=list)


SafetyFlag = Literal["green", "yellow", "red"]
Verdict = Literal["undervalued", "fair", "overvalued", "unknown"]


class MetricsReport(BaseModel):
    """Vypočtené metriky — vše odvozeno z :class:`Snapshot`."""

    ticker: str

    # A. Dividendové metriky & udržitelnost
    yield_trailing_pct: float | None = None
    yield_forward_pct: float | None = None
    payout_eps_pct: float | None = None        # DPS_TTM / EPS_TTM (u REIT ignorováno)
    fcf_payout_pct: float | None = None        # Dividends Paid / FCF
    ffo_payout_pct: float | None = None        # REIT: Dividends Paid / FFO (FFO ≈ NI + D&A)
    affo_payout_pct: float | None = None       # REIT: Dividends Paid / AFFO (AFFO ≈ FFO − CapEx, odhad)
    cagr_3y_pct: float | None = None
    cagr_5y_pct: float | None = None
    cagr_10y_pct: float | None = None
    streak_years: int = 0  # po sobě jdoucí roky růstu výplaty

    # B. Ocenění & valuace
    pe_ttm: float | None = None
    pe_forward: float | None = None
    p_b: float | None = None                  # cena / účetní hodnota na akcii (equity ÷ shares)
    graham_number: float | None = None        # √(22,5 × EPS_TTM × BVPS) — Benjaminoва férová cena
    lynch_peg: float | None = None            # P/E ÷ růst % (růst = 5letý CAGR dividendy jako proxy)
    p_fcf: float | None = None
    ev_ebitda: float | None = None
    fcf_yield_pct: float | None = None         # FCF per share / cena
    yield_5y_avg_pct: float | None = None      # průměr ročních dividendových výnosů (max. 5 let)
    yield_vs_5y_deviation_pct: float | None = None  # (aktuální/avg - 1) v %

    # C. Finanční zdraví & solvence
    net_debt_to_ebitda: float | None = None
    interest_coverage: float | None = None
    roic_pct: float | None = None

    notes: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Skóre bloků 0–100 (bloková váha je součást celkového vzorce)."""

    sustainability_pct: float | None = None  # váha 40
    valuation_pct: float | None = None       # váha 35
    quality_growth_pct: float | None = None  # váha 25


class ScoreResult(BaseModel):
    """Celkové skóre + bezpečnostní flag + signály."""

    applicable: bool = True
    total: float | None = None  # 0–100; None, pokud firma nevyplácí dividendu
    safety_flag: SafetyFlag | None = None
    signals: list[str] = Field(default_factory=list)  # strojově čitelné kódy, viz scoring.SIGNALS
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    reasons: list[str] = Field(default_factory=list)


DdmModel = Literal["gordon", "two_stage"]


class FairValueResult(BaseModel):
    """Férová cena a doporučená nákupní zóna."""

    required_return_pct: float = 8.0
    growth_assumed_pct: float | None = None
    ddm_model: DdmModel = "gordon"   # použitý dividendový diskontní model
    gordon_price: float | None = None       # single-stage (P = D1/(r−g))
    two_stage_price: float | None = None    # two-stage DDM (vysoký krátkodobý růst, g > r)
    yield_based_price: float | None = None
    fair_value: float | None = None
    buy_zone_low: float | None = None   # konzervativní kraj (15 % marže bezpečí pod FV)
    buy_zone_high: float | None = None  # optimistická kraj == férová cena
    verdict: Verdict = "unknown"           # porovnání aktuální ceny s FV (pásmo ±10 %)
    notes: list[str] = Field(default_factory=list)


class Benchmark(BaseModel):
    """Odvětvový benchmark z peer universe (mediány)."""

    sector: str
    median_pe_ttm: float | None = None
    median_ev_ebitda: float | None = None
    n_peers: int = 0
