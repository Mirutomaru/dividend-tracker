"""Upozornění na cenu / nákupní zónu.

Pravidla se ukládají do tabulky ``alerts`` a vyhodnocují se **pouze proti
aktuálním cenám v lokální DB** (tabulka ``quotes``) — žádné externí API volání,
takhle je kontrola levná a dá se spouštět i několikrát denně.

Podmínky:
- ``below``    — cena klesla na/below prahovou hodnotu (nákupní příležitost)
- ``above``    — cena vystoupala nad prahovou hodnotu (např. prodejní signál)
- ``buy_zone`` — cena se dostala do nákupní zóny; horní hranice = férová cena
  podle výnosové metody ``FV = DPS_TTM / r`` (r = nastavení ``required_return_pct``,
  výchozí 8 %), konzistentně s doménovým modulem :mod:`domain.valuation`.

Anti-duplikace: upozornění se vyhodnotí **maximálně jednou denně** — datum
posledního vyhodnocení se zapisuje do ``last_triggered``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .db import Database
from .watchlist import _build_series, _ttm

log = logging.getLogger(__name__)

VALID_CONDITIONS = ("below", "above", "buy_zone")
DEFAULT_REQUIRED_RETURN_PCT = 8.0


def _required_return_pct(db: Database) -> float:
    """Požadovaná výkonnost pro buy_zone (nastavení, výchozí 8 %)."""
    raw = db.get_setting("required_return_pct")
    try:
        val = float(raw) if raw is not None else DEFAULT_REQUIRED_RETURN_PCT
    except ValueError:
        log.warning("Nevyčíslitelné nastavení required_return_pct=%r — použito %s",
                    raw, DEFAULT_REQUIRED_RETURN_PCT)
        return DEFAULT_REQUIRED_RETURN_PCT
    return val if 0 < val <= 50 else DEFAULT_REQUIRED_RETURN_PCT


def _ttm_dps(db: Database, ticker: str) -> float:
    """TTM DPS přímo z DB (dividends_history)."""
    series = _build_series(db.get_dividends(ticker))
    return _ttm(series) if series is not None else 0.0


def _check_condition(
    condition: str,
    threshold: Optional[float],
    price: float,
    db: Database,
    ticker: str,
) -> Tuple[bool, str]:
    """Vrátí ``(vyhodnoceno?, detail pro zprávu)``."""
    if condition == "below":
        hit = threshold is not None and price <= threshold
        return hit, f"cena {price:.2f} ≤ prahová {threshold:.2f}"

    if condition == "above":
        hit = threshold is not None and price >= threshold
        return hit, f"cena {price:.2f} ≥ prahová {threshold:.2f}"

    if condition == "buy_zone":
        dps = _ttm_dps(db, ticker)
        r_pct = _required_return_pct(db)
        if dps <= 0:
            return False, ""
        fair_value = round(dps / (r_pct / 100.0), 2)
        hit = price <= fair_value
        detail = f"cena {price:.2f} ≤ férová {fair_value:.2f} (DPS_TTM {dps:.2f}, r={r_pct:g}%)"
        return hit, detail

    log.warning("Neznámá podmínka upozornění: %r", condition)
    return False, ""


def evaluate_alerts(db: Database) -> List[Dict[str, Any]]:
    """Vyhodnotí všechna aktivní upozornění proti cenám z DB.

    Vrací seznam vyhodnocených (triggered) upozornění; každé se označí
    ``last_triggered = dnes``, aby se stejný den neopakovalo. Upozornění bez
    aktuální ceny v DB se přeskočí (není co porovnávat).
    """
    today = date.today().isoformat()
    triggered: List[Dict[str, Any]] = []

    for alert in db.list_alerts(active_only=True):
        if alert.get("last_triggered") == today:
            continue  # dnes už vyhodnoceno — anti-duplikace

        quote = db.get_quote(alert["ticker"]) or {}
        price = quote.get("current_price")
        if not price or price <= 0:
            log.debug("Alert %s (%s): žádná aktuální cena v DB — přeskočeno",
                      alert["id"], alert["ticker"])
            continue

        hit, detail = _check_condition(
            alert["condition"], alert.get("threshold"), float(price), db, alert["ticker"]
        )
        if not hit:
            continue

        triggered.append({
            "id": alert["id"],
            "ticker": alert["ticker"],
            "condition": alert["condition"],
            "threshold": alert.get("threshold"),
            "price": float(price),
            "detail": detail,
            "message": _format_message(alert, float(price), detail),
        })
        db.mark_alert_triggered(alert["id"], today)

    return triggered


def _format_message(alert: Dict[str, Any], price: float, detail: str) -> str:
    """Čitelná zpráva pro oznámení (CLI / Telegram)."""
    cond = alert["condition"]
    if cond == "below":
        head = f"📉 {alert['ticker']} klesl pod prahovou cenu"
    elif cond == "above":
        head = f"📈 {alert['ticker']} vystoupal nad prahovou cenu"
    else:  # buy_zone
        head = f"🎯 {alert['ticker']} je v nákupní zóně"
    return f"{head}: {detail}"


def add_alert(db: Database, ticker: str, condition: str, threshold: Optional[float] = None) -> int:
    """Přidá upozornění s validací podmínek a prahové hodnoty."""
    if condition not in VALID_CONDITIONS:
        raise ValueError(f"Neplatná podmínka {condition!r} — povolené: {', '.join(VALID_CONDITIONS)}")
    if condition != "buy_zone":
        if threshold is None or threshold <= 0:
            raise ValueError(f"Podmínka {condition!r} vyžaduje kladnou prahovou hodnotu.")
    return db.add_alert(ticker, condition, threshold)


__all__ = [
    "VALID_CONDITIONS",
    "DEFAULT_REQUIRED_RETURN_PCT",
    "add_alert",
    "evaluate_alerts",
]
