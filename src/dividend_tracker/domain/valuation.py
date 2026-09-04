"""Valuační modul — férová cena (Gordon / two-stage DDM + výnosová metoda) a nákupní zóna."""

from __future__ import annotations

from typing import List, Optional

from ..models import FairValueResult, MetricsReport, Snapshot


def two_stage_ddm_price(
    dps: float,
    g1_pct: float,
    r_pct: float,
    n_years: int = 5,
    terminal_growth_pct: Optional[float] = None,
) -> Optional[float]:
    """Two-stage dividend discount model.

    Fáze 1 (roky 1..``n_years``): dividenda ročně roste o ``g1_pct`` %.
    Fáze 2 (od roku ``n_years + 1``): konstantní terminální růst ``terminal_growth_pct`` %
    (výchozí: polovina g1, strop 3 %, spodní hranice 0 %).

    ``P = Σ_{t=1..n} D_t / (1+r)^t + [D_{n+1} / (r − g_t)] / (1+r)^n``

    Vrací ``None``, pokud jsou vstupy neplatné nebo ``r <= g_t``
    (terminální hodnota by divergovala). Funkce je čistá — žádné skryté ořezávání.
    """
    if dps is None or dps <= 0:
        return None
    if r_pct is None or r_pct <= 0:
        return None
    if n_years < 1:
        return None

    g1 = g1_pct / 100.0
    r = r_pct / 100.0
    gt = (terminal_growth_pct if terminal_growth_pct is not None else min(max(g1_pct / 2.0, 0.0), 3.0)) / 100.0

    if r <= gt:
        return None

    price = 0.0
    d_t = dps
    for t in range(1, n_years + 1):
        d_t *= 1.0 + g1          # dividenda v roce t: D_t = DPS·(1+g1)^t
        price += d_t / (1.0 + r) ** t
    terminal_value = d_t * (1.0 + gt) / (r - gt)   # TV na konci roku n = D_{n+1}/(r−g_t)
    price += terminal_value / (1.0 + r) ** n_years
    return price


def compute_fair_value(
    snap: Snapshot, m: MetricsReport, required_return_pct: float = 8.0
) -> FairValueResult:
    """Odhad férové ceny a doporučená nákupní zóna.

    - **Gordon Growth Model (DDM)**: ``P = D1 / (r − g)``, kde ``D1 = DPS_TTM·(1+g)``,
      ``r`` = požadovaná výkonnost (výchozí 8 %), ``g`` = očekávaný růst dividendy
      (5letý CAGR oříznutý na [−2 %, +6 %]).
    - **Two-stage DDM**: pokud je 5letý CAGR ≥ požadovaná výkonnost (g ≥ r, kde Gordon
      diverguje), použije se two-stage model — fáze 1: růst = CAGR oříznutý na [0 %, +15 %]
      po dobu 5 let, fáze 2: terminální růst min(g1/2, 3 %). Pokud ani ten nekonverguje
      (r ≤ g_t), spadne se na konzervativní ořez Gordonu.
    - **Výnosová metoda**: cena, při které by forward DPS dávala 5letý průměrný
      dividendový výnos: ``P = ForwardDPS / avg_yield_5y``.
    - Férová cena = průměr dostupných odhadů; nákupní zóna = [0.85·FV, FV] (15 % marže bezpečí).
    """
    notes: List[str] = []
    r = required_return_pct / 100.0

    dps = snap.dps_ttm or 0.0
    if dps <= 0 or not snap.price:
        return FairValueResult(
            required_return_pct=required_return_pct,
            verdict="unknown",
            notes=["Férová cena nepočítatelná — chybí DPS_TTM nebo aktuální cena."],
        )

    ddm_model = "gordon"
    gordon_price: Optional[float] = None
    two_stage_price: Optional[float] = None
    growth_assumed_pct: Optional[float] = None

    cagr5_raw = m.cagr_5y_pct  # číselná procenta, může být None
    if cagr5_raw is not None and cagr5_raw / 100.0 >= r:
        # ---- vysoký růst (g ≥ r): two-stage DDM ---------------------------------
        g1 = min(max(cagr5_raw, 0.0), 15.0)
        two_stage_price = two_stage_ddm_price(dps, g1, required_return_pct)
        if two_stage_price is not None:
            ddm_model = "two_stage"
            growth_assumed_pct = round(g1, 2)
            notes.append(
                f"CAGR 5Y {cagr5_raw:.1f} % ≥ požadovaná výkonnost {required_return_pct:.1f} % — "
                f"použit two-stage DDM (5y @ {g1:.1f} %, terminální růst "
                f"{min(max(g1 / 2.0, 0.0), 3.0):.1f} %)."
            )
        else:
            notes.append(
                "Two-stage DDM nekonverguje (r ≤ terminální růst) — "
                "konzervativní ořez Gordonu."
            )

    if ddm_model == "gordon":
        cagr5 = (cagr5_raw or 0.0) / 100.0
        g = min(max(cagr5, -0.02), 0.06)
        growth_assumed_pct = round(g * 100.0, 2)
        if cagr5_raw is None:
            notes.append("CAGR 5Y k dispozici není — předpokládán růst dividendy 0 %.")
        if g >= r:
            g = max(0.0, r - 0.03)
            growth_assumed_pct = round(g * 100.0, 2)
            notes.append(f"Růst g={cagr5:.1%} ≥ požadovaná výkonnost {r:.1%} — konzervativní ořez na {g:.1%}.")

        d1 = dps * (1.0 + g)
        gordon_price = d1 / (r - g) if r > g else None
        if gordon_price is not None and d1 / gordon_price * 100.0 > 25.0:
            notes.append("Pozor: implikovaný výnos u Gordon FV > 25 % — model pravděpodobně nereálný.")

    # ---- Výnosová metoda ----------------------------------------------------------
    yield_based_price: Optional[float] = None
    avg_yield_pct = m.yield_5y_avg_pct
    if avg_yield_pct is None or avg_yield_pct <= 0:
        avg_yield_pct = m.yield_trailing_pct
        notes.append("5Y průměrný výnos nedostupný — použito aktuální trailing yield jako aproximace.")
    fwd_dps = snap.forward_dividend_rate if (snap.forward_dividend_rate or 0) > 0 else dps
    if avg_yield_pct and avg_yield_pct > 0:
        yield_based_price = fwd_dps / (avg_yield_pct / 100.0)

    # ---- Férová cena, nákupní zóna, verdikt ---------------------------------------
    ddm_estimate = two_stage_price if ddm_model == "two_stage" else gordon_price
    estimates = [p for p in (ddm_estimate, yield_based_price) if p is not None and p > 0]
    fair_value = sum(estimates) / len(estimates) if estimates else None
    buy_low = round(fair_value * 0.85, 2) if fair_value else None
    buy_high = round(fair_value, 2) if fair_value else None

    verdict: str = "unknown"
    if fair_value and snap.price > 0:
        ratio = snap.price / fair_value
        if ratio < 0.9:
            verdict = "undervalued"
        elif ratio <= 1.1:
            verdict = "fair"
        else:
            verdict = "overvalued"

    return FairValueResult(
        required_return_pct=required_return_pct,
        growth_assumed_pct=growth_assumed_pct,
        ddm_model=ddm_model,  # type: ignore[arg-type]
        gordon_price=round(gordon_price, 2) if gordon_price else None,
        two_stage_price=round(two_stage_price, 2) if two_stage_price else None,
        yield_based_price=round(yield_based_price, 2) if yield_based_price else None,
        fair_value=round(fair_value, 2) if fair_value else None,
        buy_zone_low=buy_low,
        buy_zone_high=buy_high,
        verdict=verdict,  # type: ignore[arg-type]
        notes=notes,
    )
