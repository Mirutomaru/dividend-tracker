"""Klient Tiingo API — ceny, historie cen a dividendová historie.

Tiingo doplňuje yfinance jako zdroj s **přísným, ale velkým denním limitem**
(10 000 volání/den na bezplatném plánu; limit je přepisovatelný přes
``TIINGO_DAILY_LIMIT``). Eviduje se v tabulce ``api_usage`` stejně jako FMP —
před každým voláním se kontroluje zbývající počet a při vyčerpání se sync
přepne na yfinance fallback.

Endpointy (https://www.tiingo.com/documentation):
- ``GET /tiingo/daily/{ticker}`` — metadata tickeru (bez datových parametrů)
  nebo denní OHLCV bary s ``startDate``/``endDate`` (YYYY-MM-DD).
- ``GET /tiingo/corporate-actions/{ticker}/dividends`` — kompletní historie
  distribucí včetně budoucích (exDate, paymentDate, recordDate, distribution).

API klíč se čte z proměnné prostředí ``TIINGO_API_KEY``; bez něj klient hlásí
nekonfiguraci a volající přepne na jiný provider. Žádné nové závislosti —
čistý ``urllib`` (stejně jako :mod:`dividend_tracker.data.fmp`).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .db import Database

log = logging.getLogger(__name__)

TIINGO_BASE_URL = "https://api.tiingo.com/tiingo"
#: Výchozí denní limit volání (bezplatný plán Tiingo). Přepisovatelné env.
DAILY_LIMIT = 10_000
PROVIDER_NAME = "tiingo"

#: Mapování kódů frekvence distribucí Tiingo na srozumitelné hodnoty pro DB.
_FREQUENCY_MAP = {
    "w": "weekly",
    "bm": "bimonthly",
    "m": "monthly",
    "tm": "trimesterly",
    "q": "quarterly",
    "sa": "semiannual",
    "a": "annual",
    "ir": "irregular",
    "f": "final",
    "u": None,  # unspecified → neukládáme nic
    "c": "cancelled",
}


class TiingoError(RuntimeError):
    """Chyba volání Tiingo API (síť, HTTP, neplatná odpověď)."""


class TiingoLimitExceeded(TiingoError):
    """Denní limit volání vyčerpán — data se už dnes netaží z Tiingo."""


def get_api_key() -> Optional[str]:
    return os.environ.get("TIINGO_API_KEY", "").strip() or None


def _daily_limit() -> int:
    raw = os.environ.get("TIINGO_DAILY_LIMIT", "").strip()
    try:
        return max(1, int(raw)) if raw else DAILY_LIMIT
    except ValueError:
        log.warning("TIINGO_DAILY_LIMIT=%r není číslo — používám výchozí %d", raw, DAILY_LIMIT)
        return DAILY_LIMIT


def _get_json(url: str, api_key: str, timeout: float = 15.0) -> Any:
    """GET s Bearer autentizací; 429 → :class:`TiingoLimitExceeded`, ostatní HTTP → :class:`TiingoError`."""
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "User-Agent": "dividend-tracker/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise TiingoLimitExceeded("Tiingo vrátil HTTP 429 (rate limit).") from exc
        if exc.code in (401, 403):
            raise TiingoError(f"Tiingo HTTP {exc.code} — zkontroluj TIINGO_API_KEY.") from exc
        raise TiingoError(f"Tiingo HTTP {exc.code} pro {url.split('?')[0]}") from exc
    except urllib.error.URLError as exc:
        raise TiingoError(f"síťová chyba při volání Tiingo: {exc.reason}") from exc


class TiingoCacheClient:
    """Tiingo klient s evidencí denních volání přes :class:`Database`.

    Každé úspěšné volání se zapíše do ``api_usage`` (429/5xx = zdarma, stejně
    jako u FMP). Metoda :meth:`can_call` je brána před dalším dotazem — při
    vyčerpání limitu se vrací ``False`` a sync přejde na fallback provider.
    """

    def __init__(self, db: Database, api_key: Optional[str] = None) -> None:
        self.db = db
        self.api_key = api_key if api_key is not None else get_api_key()

    # ------------------------------------------------------------------ limity

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def usage(self) -> Dict[str, Any]:
        """Aktuální stav čítače (calls_used / daily_limit / remaining)."""
        u = self.db.get_api_usage(PROVIDER_NAME)
        if u.get("daily_limit") is None:
            u["daily_limit"] = _daily_limit()
        return u

    def can_call(self) -> bool:
        if not self.configured:
            return False
        u = self.usage()
        limit, used = u.get("daily_limit"), u.get("calls_used", 0)
        if limit is None:
            return True
        return used < limit

    def _consume(self) -> None:
        """Zapíše jedno volání; při překročení limitu zvedne :class:`TiingoLimitExceeded`."""
        u = self.db.record_api_call(PROVIDER_NAME, daily_limit=_daily_limit())
        if u.get("remaining") is not None and u["remaining"] < 0:
            raise TiingoLimitExceeded(
                f"Tiingo denní limit {u['daily_limit']} volání vyčerpán (použito {u['calls_used']})."
            )

    # ------------------------------------------------------------------ dotazy

    def _fetch(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.can_call():
            raise TiingoLimitExceeded(
                f"Tiingo denní limit {_daily_limit()} volání vyčerpán — čtu pouze z DB."
            )
        qs = urllib.parse.urlencode(params or {})
        url = f"{TIINGO_BASE_URL}/{path}" + (f"?{qs}" if qs else "")
        data = _get_json(url, self.api_key)  # type: ignore[arg-type]
        self._consume()  # volání se počítá až po úspěšném dotazu (429/5xx = zdarma)
        return data

    def get_meta(self, ticker: str) -> Dict[str, Any]:
        """Metadata tickeru (name, currency, exchange). Prázdný dict = neznámý symbol."""
        data = self._fetch(f"daily/{ticker.strip().upper()}")
        if isinstance(data, list):  # některé verze API vrátí bary i bez datových parametrů
            return {}
        return data if isinstance(data, dict) else {}

    def get_price_history(
        self, ticker: str, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Denní OHLCV bary (nejstarší první). ``start_date``/``end_date`` jako ISO YYYY-MM-DD."""
        params: Dict[str, Any] = {}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        data = self._fetch(f"daily/{ticker.strip().upper()}", params)
        bars = data if isinstance(data, list) else []
        return sorted(bars, key=lambda b: str(b.get("date", "")))

    def get_dividends(self, ticker: str) -> List[Dict[str, Any]]:
        """Kompletní historie distribucí včetně budoucích (exDate, distribution, …)."""
        data = self._fetch(f"corporate-actions/{ticker.strip().upper()}/dividends")
        return data if isinstance(data, list) else []


def recent_window(days: int = 10) -> tuple[str, str]:
    """Okno pro dotaz na aktuální cenu (posledních ``days`` dní) — ISO datum startu a konec."""
    today = date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


__all__ = [
    "TiingoError",
    "TiingoLimitExceeded",
    "TiingoCacheClient",
    "DAILY_LIMIT",
    "_FREQUENCY_MAP",
    "get_api_key",
    "recent_window",
]
