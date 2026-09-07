"""Výkonnostní metriky portfolia — TWR a IRR. Čisté funkce, bez sítě i DB.

Konvence cash flow (``amount`` / ``external_flows``):
- **buy** = záporné (peníze odcházejí z portfolia),
- **sell / dividend** = kladné (peníze přicházejí investorovi).

TWR (time-weighted return) — standardní formulace:
    r_k = [V(t_k) − W(t_{k−1})] / W(t_{k−1}),  W(t) = V(t) − CF(t)
    TWR = Π(1 + r_k) − 1
kde ``V(t)`` je tržní hodnota portfolia v den ``t`` (těsně PŘED vyplacením
externího toku toho dne) a ``CF(t)`` externí tok (dividenda/prodej = kladné).
TWR očišťuje výkonnost od vkladů/výběrů — porovnatelné s benchmarkem.

IRR (internal rate of return) — roční sazba r, při které je NPV všech cash flow
včetně konečné tržní hodnoty nulové:
    Σ_k CF_k / (1+r)^{t_k} + MV_today / (1+r)^{T} = 0
Řeší se bisekcí na intervalu [−99 %, +500 %] ročně.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CashFlow:
    """Jeden externí cash flow portfolia (nákup/prodej/dividenda)."""

    date_iso: str  # YYYY-MM-DD
    amount: float  # signed: buy < 0, sell/dividend > 0


def _years_between(date_from_iso: str, date_to_iso: str) -> float:
    """Počet let mezi dvěma ISO daty (365.25 dnů/rok)."""
    d1 = date.fromisoformat(date_from_iso[:10])
    d2 = date.fromisoformat(date_to_iso[:10])
    return max((d2 - d1).days / 365.25, 0.0)


def time_weighted_return(
    value_series: List[Tuple[str, float]],
    external_flows: Dict[str, float],
) -> Optional[float]:
    """Chain-linking TWR v procentech (např. 12.3 == +12,3 %).

    ``value_series`` = seřazená dvojice ``(datum, V)`` — tržní hodnota portfolia
    v daný den **po** aplikaci transakcí toho dne; poslední bod má být aktuální
    hodnota. Každé datum externího toku musí být v řadě (vrstva nahoře to zajistí).
    ``external_flows`` = {datum: čistý externí tok}, kladné = peníze do kapsy
    investora (dividendy/prodeje), záporné = nákupy.

    Pro každé období končící v ``t_k``: r_k = [V(t_k) + CF_out(t_k) − V(t_{k−1})] / V(t_{k−1}),
    TWR = Π(1 + r_k) − 1. Vrátí None, pokud chybí data (méně než 2 body, V ≤ 0).
    """
    points = sorted((d, v) for d, v in value_series if v and v > 0)
    if len(points) < 2:
        return None

    product = 1.0
    prev_date, prev_value = points[0]
    for date_i, value_i in points[1:]:
        if date_i <= prev_date:
            continue
        # Hodnota těsně PŘED externím tokem dne t_k (CF_out kladné = peníze ven).
        v_pre = value_i + external_flows.get(date_i, 0.0)
        if not math.isfinite(v_pre) or v_pre <= 0:
            return None
        product *= v_pre / prev_value
        prev_date, prev_value = date_i, value_i

    if not math.isfinite(product) or product <= 0:
        return None
    return round((product - 1.0) * 100.0, 2)


def internal_rate_of_return(
    cashflows: List[CashFlow],
    terminal_value: Optional[float],
    as_of_iso: str,
) -> Optional[float]:
    """Roční IRR v procentech (např. 9.8 == +9,8 % p.a.).

    ``cashflows`` = externí cash flow (buy záporné), ``terminal_value`` = současná
    tržní hodnota portfolia (připočí se jako kladný tok v ``as_of_iso``).
    Bisekce na r ∈ [−99 %, +500 %]; None, pokud neexistuje kořen nebo chybí data.
    """
    flows = sorted(cashflows, key=lambda c: c.date_iso)
    if not flows or terminal_value is None or terminal_value <= 0:
        return None

    t0 = flows[0].date_iso[:10]
    # Cash flow v čase (roky od prvního toku); terminální hodnota na konci.
    terms: List[Tuple[float, float]] = [(_years_between(t0, c.date_iso), c.amount) for c in flows]
    t_end = max(_years_between(t0, as_of_iso[:10]), 1e-9)
    terms.append((t_end, terminal_value))

    def npv(rate: float) -> float:
        total = 0.0
        for years, amount in terms:
            if rate <= -1.0:
                return math.inf
            total += amount / (1.0 + rate) ** years
        return total

    lo, hi = -0.99, 5.0
    f_lo, f_hi = npv(lo), npv(hi)
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
        return None
    if f_lo * f_hi > 0:
        # Žádný kořen v intervalu — zkusit rozšířit horní hranici.
        hi = 20.0
        f_hi = npv(hi)
        if not math.isfinite(f_hi) or f_lo * f_hi > 0:
            return None

    for _ in range(80):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            lo = hi = mid
            break
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid

    rate = (lo + hi) / 2.0
    if not math.isfinite(rate):
        return None
    return round(rate * 100.0, 2)


__all__ = ["CashFlow", "time_weighted_return", "internal_rate_of_return"]
