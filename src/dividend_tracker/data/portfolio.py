"""Portfolio — držené pozice, dividendový příjem a kalkulačka cílového příjmů.

Vše se počítá **přímo z lokální DB** (bez externích volání):
- pozice z tabulky ``portfolio`` (ticker, shares, avg_cost)
- aktuální cena z ``quotes``, TTM DPS z ``dividends_history``

Hlavní metriky:
- tržní hodnota a nákladová báze každé pozice + nerealizovaný P&L
- **roční dividendový příjem** = Σ (shares × TTM DPS)
- **čistý roční příjem po zdanění** — sražení zadržovací daně podle země
  původu emitenta (sazby v ``domain/tax.py``, přepsatelné nastavením
  ``withholding_<ZEME>`` v DB)
- portfolio yield (příjem / tržní hodnota) a vážený yield on cost
- **kalkulačka cíle**: při zadaném ročním příjmu spočítá potřebný kapitál
  (cíl ÷ očekávaný yield) a ukáže progres portfolia k cíli.

Poznámka: příjem se počítá v měně jednotlivých pozic; pokud portfolio obsahuje
více měn, součet je „smíšený“ — frontend to označí. Pro rentier plán v jedné
měně (typicky USD) je výsledek přesný. Daňové sazby jsou odhad — pro přesné
daňové poradenství se poraďte s daňovým poradcem.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..domain.tax import net_amount, withholding_rate
from .db import Database
from .watchlist import _build_series, _ttm

log = logging.getLogger(__name__)


def build_portfolio_report(db: Database) -> Dict[str, Any]:
    """Vrátí kompletní report portfolia (pozice + součty)."""
    positions: List[Dict[str, Any]] = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    total_annual_income = 0.0
    total_net_annual_income = 0.0
    income_by_currency: Dict[str, float] = {}

    # Přepsání sazeb z databáze (sazby lze kdykoli upravit bez kódu).
    overrides = {k: v for k, v in db.list_settings().items() if k.startswith("withholding_")}

    for pos in db.list_positions():
        tk = pos["ticker"]
        shares = float(pos.get("shares") or 0)
        avg_cost = pos.get("avg_cost")
        currency = pos.get("currency") or "USD"

        stock = db.get_stock(tk) or {}
        country = stock.get("country")

        quote = db.get_quote(tk) or {}
        price = quote.get("current_price")

        series = _build_series(db.get_dividends(tk))
        dps_ttm = _ttm(series)

        market_value = shares * price if (price and price > 0) else None
        cost_basis = shares * avg_cost if (avg_cost and avg_cost > 0) else None
        annual_income = round(shares * dps_ttm, 2) if dps_ttm > 0 else 0.0

        # Čistý příjem po zdanění — sazba podle země původu emitenta.
        rate_pct = withholding_rate(country, overrides)
        net_raw = net_amount(annual_income, country, overrides) if annual_income > 0 else None
        net_annual_income = round(net_raw, 2) if net_raw is not None else 0.0

        yield_on_cost: Optional[float] = None
        if avg_cost and avg_cost > 0 and dps_ttm > 0:
            yield_on_cost = round(dps_ttm / avg_cost * 100.0, 2)

        unrealized_pl: Optional[float] = None
        unrealized_pl_pct: Optional[float] = None
        if market_value is not None and cost_basis is not None:
            unrealized_pl = round(market_value - cost_basis, 2)
            if cost_basis > 0:
                unrealized_pl_pct = round((market_value / cost_basis - 1.0) * 100.0, 2)

        positions.append(
            {
                "ticker": tk,
                "shares": shares,
                "avg_cost": avg_cost,
                "currency": currency,
                "country": country,
                "price": price,
                "market_value": round(market_value, 2) if market_value is not None else None,
                "cost_basis": round(cost_basis, 2) if cost_basis is not None else None,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "dps_ttm": dps_ttm or None,
                "annual_income": annual_income,
                "withholding_rate_pct": rate_pct,
                "net_annual_income": net_annual_income,
                "yield_on_cost_pct": yield_on_cost,
            }
        )

        if market_value is not None:
            total_market_value += market_value
        if cost_basis is not None:
            total_cost_basis += cost_basis
        total_annual_income += annual_income
        total_net_annual_income += net_annual_income
        income_by_currency[currency] = round(income_by_currency.get(currency, 0.0) + annual_income, 2)

    portfolio_yield_pct: Optional[float] = None
    if total_market_value > 0 and total_annual_income > 0:
        portfolio_yield_pct = round(total_annual_income / total_market_value * 100.0, 2)

    net_portfolio_yield_pct: Optional[float] = None
    if total_market_value > 0 and total_net_annual_income > 0:
        net_portfolio_yield_pct = round(total_net_annual_income / total_market_value * 100.0, 2)

    weighted_yield_on_cost_pct: Optional[float] = None
    if total_cost_basis > 0 and total_annual_income > 0:
        weighted_yield_on_cost_pct = round(total_annual_income / total_cost_basis * 100.0, 2)

    return {
        "positions": positions,
        "n_positions": len(positions),
        "total_market_value": round(total_market_value, 2) if total_market_value else None,
        "total_cost_basis": round(total_cost_basis, 2) if total_cost_basis else None,
        "unrealized_pl_total": (
            round(total_market_value - total_cost_basis, 2)
            if (total_market_value and total_cost_basis)
            else None
        ),
        "annual_income": round(total_annual_income, 2),
        "net_annual_income": round(total_net_annual_income, 2),
        "income_by_currency": income_by_currency or None,
        "portfolio_yield_pct": portfolio_yield_pct,
        "net_portfolio_yield_pct": net_portfolio_yield_pct,
        "weighted_yield_on_cost_pct": weighted_yield_on_cost_pct,
    }


def build_income_target_report(db: Database) -> Dict[str, Any]:
    """Kalkulačka cílového ročního příjmů (rentier plán).

    Čte nastavení ``income_target`` (roční příjem v měně portfolia) a volitelně
    ``target_yield_pct`` (očekávaný yield pro výpočet potřebného kapitálu,
    výchozí 4 %). Pokud portfolio už má reálný yield, použije se ten.
    """
    target_raw = db.get_setting("income_target")
    if not target_raw:
        return {"configured": False}

    try:
        target = float(target_raw)
    except ValueError:
        log.warning("nastavení income_target není číslo: %r", target_raw)
        return {"configured": False}

    yield_raw = db.get_setting("target_yield_pct")
    assumed_yield_pct: Optional[float] = None
    if yield_raw:
        try:
            assumed_yield_pct = float(yield_raw)
        except ValueError:
            pass

    report = build_portfolio_report(db)
    current_income = report["annual_income"] or 0.0
    portfolio_yield = report["portfolio_yield_pct"]

    # Pro výpočet potřebného kapitálu použijeme reálný yield portfolia,
    # jinak nastavený/odhadovaný.
    effective_yield_pct = portfolio_yield if (portfolio_yield and portfolio_yield > 0) else assumed_yield_pct or 4.0

    required_capital: Optional[float] = round(target / (effective_yield_pct / 100.0), 2) if target > 0 else None
    progress_pct: Optional[float] = (
        round(current_income / target * 100.0, 1) if target > 0 else None
    )
    remaining_income = round(target - current_income, 2)

    return {
        "configured": True,
        "target_annual_income": target,
        "current_annual_income": current_income,
        "remaining_income": max(remaining_income, 0.0),
        "progress_pct": progress_pct,
        "effective_yield_pct_used": effective_yield_pct,
        "required_capital": required_capital,
        "current_market_value": report["total_market_value"],
    }


__all__ = ["build_portfolio_report", "build_income_target_report"]
