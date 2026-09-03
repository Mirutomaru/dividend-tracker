"""Valuační modul — férová cena (Gordon Growth Model + výnosová metoda) a nákupní zóna."""

from __future__ import annotations

from typing import List, Optional

from ..models import FairValueResult, MetricsReport, Snapshot


def compute_fair_value(
    snap: Snapshot, m: MetricsReport, required_return_pct: float = 8.0
) -> FairValueResult:
    """Odhad férové ceny a doporučená nákupní zóna.

    - **Gordon Growth Model (DDM)**: ``P = D1 / (r − g)``, kde ``D1 = DPS_TTM·(1+g)``,
      ``r`` = požadovaná výkonnost (výchozí 8 %), ``g`` = očekávaný růst dividendy
      (5letý CAGR oříznutý na [−2 %, +6 %]; při g ≥ r se konzervativně sníží).
    - **Výnosová metoda**: cena, při které by forward DPS dávala 5letý průměrný
      dividendový výnos: ``P = ForwardDPS / avg_yield_5y``.
    - Férová cena = průměr obou odhadů; nákupní zóna = [0.85·FV, FV] (15 % marže bezpečí).
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

    # ---- Gordon -----------------------------------------------------------------
    cagr5 = (m.cagr_5y_pct or 0.0) / 100.0
    g = min(max(cagr5, -0.02), 0.06)
    if m.cagr_5y_pct is None:
        notes.append("CAGR 5Y k dispozici není — předpokládán růst dividendy 0 %.")
    if g >= r:
        g = max(0.0, r - 0.03)
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
    estimates = [p for p in (gordon_price, yield_based_price) if p is not None and p > 0]
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
        growth_assumed_pct=round(g * 100.0, 2),
        gordon_price=round(gordon_price, 2) if gordon_price else None,
        yield_based_price=round(yield_based_price, 2) if yield_based_price else None,
        fair_value=round(fair_value, 2) if fair_value else None,
        buy_zone_low=buy_low,
        buy_zone_high=buy_high,
        verdict=verdict,  # type: ignore[arg-type]
        notes=notes,
    )
