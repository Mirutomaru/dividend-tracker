"""Doménové datové modely — normalizovaný snapshot a výsledky analýzy (Pydantic v2).

Konvence jednotek:
- pole s příponou ``_pct`` obsahují číselné procenta (23.4 == 23,4 %),
- poměrová pole (např. ``net_debt_to_ebitda``) jsou bezrozměrné násobky (1.5 == 1,5x).
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    """Všechna surová data o jedné společnosti na jednom místě — hranice datové vrstvy.

    Neznámé hodnoty jsou ``None`` a doplní se do :attr:`data_gaps`, aby scoring
    mohl váhu chybějících položek upravit místo tichého zero.
    """

    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: str = "USD"
    as_of: Optional[str] = None  # ISO timestamp sběru dat

    price: Optional[float] = None
    shares_outstanding: Optional[int] = None
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None

    # Dividendy (za fiskální rok; fiskální rok ≈ kalendářní rok, viz domain/dividends.py)
    annual_dps: Dict[int, float] = Field(default_factory=dict)
    dividend_payment_counts: Dict[int, int] = Field(default_factory=dict)
    dps_ttm: Optional[float] = None  # dividenda na akcii TTM (posledních 365 dnů od poslední výplaty)
    forward_dividend_rate: Optional[float] = None  # očekávaná roční DPS (forward)

    # Výsledek / vstupy valuace (nejnovější fiskální rok nebo TTM, dle providera)
    eps_ttm: Optional[float] = None
    eps_forward: Optional[float] = None
    ebitda: Optional[float] = None
    ebit: Optional[float] = None
    interest_expense: Optional[float] = None
    net_income: Optional[float] = None
    tax_rate: Optional[float] = None  # 0.25 == 25 %

    # Bilance / cash flow (nejnovější fiskální rok)
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    equity: Optional[float] = None
    fcf_ttm: Optional[float] = None  # volný hotovostní tok (roční, nejnovější k dispozici)
    dividends_paid: Optional[float] = None  # reálně vyplacené dividendy z CF výkazu
    depreciation_amortization: Optional[float] = None  # D&A z CF výkazu (pro FFO/AFFO u REIT)
    capex: Optional[float] = None  # kapitálové výdaje, kladná velikost |CapEx| (AFFO odhad)

    # Násobky hlášené direktně providerem (fallback / cross-check)
    pe_ttm_reported: Optional[float] = None
    pe_forward_reported: Optional[float] = None
    ev_ebitda_reported: Optional[float] = None

    # Historie pro kontext yieldu a P/E (fiskální rok -> hodnota)
    fiscal_year_prices: Dict[int, float] = Field(default_factory=dict)  # závěrečná cena za rok
    pe_history_approx: Dict[int, float] = Field(default_factory=dict)   # P/E aprox. (konstantní EPS_TTM!)

    data_gaps: List[str] = Field(default_factory=list)


SafetyFlag = Literal["green", "yellow", "red"]
Verdict = Literal["undervalued", "fair", "overvalued", "unknown"]


class MetricsReport(BaseModel):
    """Vypočtené metriky — vše odvozeno z :class:`Snapshot`."""

    ticker: str

    # A. Dividendové metriky & udržitelnost
    yield_trailing_pct: Optional[float] = None
    yield_forward_pct: Optional[float] = None
    payout_eps_pct: Optional[float] = None        # DPS_TTM / EPS_TTM (u REIT ignorováno)
    fcf_payout_pct: Optional[float] = None        # Dividends Paid / FCF
    ffo_payout_pct: Optional[float] = None        # REIT: Dividends Paid / FFO (FFO ≈ NI + D&A)
    affo_payout_pct: Optional[float] = None       # REIT: Dividends Paid / AFFO (AFFO ≈ FFO − CapEx, odhad)
    cagr_3y_pct: Optional[float] = None
    cagr_5y_pct: Optional[float] = None
    cagr_10y_pct: Optional[float] = None
    streak_years: int = 0  # po sobě jdoucí roky růstu výplaty

    # B. Ocenění & valuace
    pe_ttm: Optional[float] = None
    pe_forward: Optional[float] = None
    p_fcf: Optional[float] = None
    ev_ebitda: Optional[float] = None
    fcf_yield_pct: Optional[float] = None         # FCF per share / cena
    yield_5y_avg_pct: Optional[float] = None      # průměr ročních dividendových výnosů (max. 5 let)
    yield_vs_5y_deviation_pct: Optional[float] = None  # (aktuální/avg - 1) v %

    # C. Finanční zdraví & solvence
    net_debt_to_ebitda: Optional[float] = None
    interest_coverage: Optional[float] = None
    roic_pct: Optional[float] = None

    notes: List[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Skóre bloků 0–100 (bloková váha je součást celkového vzorce)."""

    sustainability_pct: Optional[float] = None  # váha 40
    valuation_pct: Optional[float] = None       # váha 35
    quality_growth_pct: Optional[float] = None  # váha 25


class ScoreResult(BaseModel):
    """Celkové skóre + bezpečnostní flag + signály."""

    applicable: bool = True
    total: Optional[float] = None  # 0–100; None, pokud firma nevyplácí dividendu
    safety_flag: Optional[SafetyFlag] = None
    signals: List[str] = Field(default_factory=list)  # strojově čitelné kódy, viz scoring.SIGNALS
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    reasons: List[str] = Field(default_factory=list)


DdmModel = Literal["gordon", "two_stage"]


class FairValueResult(BaseModel):
    """Férová cena a doporučená nákupní zóna."""

    required_return_pct: float = 8.0
    growth_assumed_pct: Optional[float] = None
    ddm_model: DdmModel = "gordon"   # použitý dividendový diskontní model
    gordon_price: Optional[float] = None       # single-stage (P = D1/(r−g))
    two_stage_price: Optional[float] = None    # two-stage DDM (vysoký krátkodobý růst, g > r)
    yield_based_price: Optional[float] = None
    fair_value: Optional[float] = None
    buy_zone_low: Optional[float] = None   # konzervativní kraj (15 % marže bezpečí pod FV)
    buy_zone_high: Optional[float] = None  # optimistická kraj == férová cena
    verdict: Verdict = "unknown"           # porovnání aktuální ceny s FV (pásmo ±10 %)
    notes: List[str] = Field(default_factory=list)


class Benchmark(BaseModel):
    """Odvětvový benchmark z peer universe (mediány)."""

    sector: str
    median_pe_ttm: Optional[float] = None
    median_ev_ebitda: Optional[float] = None
    n_peers: int = 0
