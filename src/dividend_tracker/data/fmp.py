"""Klient Financial Modeling Prep (FMP) — fundamentální data s přísným limitováním.

Pravidla (viz zadání):
- Denní limit **250 volání** se eviduje v tabulce ``api_usage``; před každým
  voláním se kontroluje zbývající počet a při vyčerpání se fundamenty
  aktualizace zastaví (pouze čtení z DB).
- Šetření dotazy: dividendová historie + plánované výplaty se stahují v dávce
  (2 endpointy na ticker), profil společnosti je volitelný třetí.
- Aktuální ceny přes FMP **neprotékají** — ty patří do :mod:`dividend_tracker.data.quotes`.

API klíč se čte z proměnné prostředí ``FMP_API_KEY``; bez něj klient hlásí
nekonfiguraci a sync automaticky přepne na fallback (yfinance).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .db import Database

log = logging.getLogger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
DAILY_LIMIT = 250
PROVIDER_NAME = "fmp"


class FMPError(RuntimeError):
    """Chyba volání FMP API (síť, HTTP, neplatná odpověď)."""


class FMPLimitExceeded(FMPError):
    """Denní limit volání vyčerpán — fundamenty se už dnes netaží."""


def get_api_key() -> Optional[str]:
    return os.environ.get("FMP_API_KEY", "").strip() or None


def _get_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "dividend-tracker/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise FMPLimitExceeded("FMP vrátil HTTP 429 (rate limit).") from exc
        raise FMPError(f"FMP HTTP {exc.code} pro {url.split('?')[0]}") from exc
    except urllib.error.URLError as exc:
        raise FMPError(f"síťová chyba při volání FMP: {exc.reason}") from exc


class FMPCacheClient:
    """FMP klient s evidencí denních volání přes :class:`Database`.

    Každé úspěšné i neúspěšné (počítané) volání se zapíše do ``api_usage``.
    Metoda :meth:`can_call` je brána před dalším dotazem — při vyčerpání limitu
    se vrací ``False`` a sync přejde na čtení z DB / fallback zdroj.
    """

    def __init__(self, db: Database, api_key: Optional[str] = None) -> None:
        self.db = db
        self.api_key = api_key if api_key is not None else get_api_key()

    # ------------------------------------------------------------------ limity

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def usage(self) -> Dict[str, Any]:
        """Aktuální stav čítače (calls_used / daily_limit / remaining / expires_at)."""
        return self.db.get_api_usage(PROVIDER_NAME)

    def can_call(self) -> bool:
        if not self.configured:
            return False
        u = self.usage()
        limit, used = u.get("daily_limit"), u.get("calls_used", 0)
        if limit is None:
            return True
        return used < limit

    def _consume(self) -> None:
        """Zapíše jedno volání; při překročení limitu zvedne :class:`FMPLimitExceeded`."""
        u = self.db.record_api_call(PROVIDER_NAME, daily_limit=DAILY_LIMIT)
        if u.get("remaining") is not None and u["remaining"] < 0:
            raise FMPLimitExceeded(
                f"FMP denní limit {DAILY_LIMIT} volání vyčerpán (použito {u['calls_used']})."
            )

    # ------------------------------------------------------------------ dotazy

    def _fetch(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.can_call():
            raise FMPLimitExceeded(
                f"FMP denní limit {DAILY_LIMIT} volání vyčerpán — čtu pouze z DB."
            )
        qs = urllib.parse.urlencode({**params, "apikey": self.api_key})
        data = _get_json(f"{FMP_BASE_URL}/{endpoint}?{qs}")
        if isinstance(data, dict) and data.get("Error Message"):
            raise FMPError(f"FMP vrátilo chybu: {data['Error Message']}")
        self._consume()  # volání se počítá až po úspěšném dotazu (429/5xx = zdarma)
        return data if isinstance(data, list) else []

    def get_dividend_history(self, ticker: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Kompletní historie vyplacených dividend (ex_date, paymentDate, amount)."""
        return self._fetch("historical-dividend", {"symbol": ticker.upper(), "limit": limit})

    def get_dividend_calendar(self, ticker: str) -> List[Dict[str, Any]]:
        """Plánované budoucí výplaty (ex-dateDividends pro daný symbol)."""
        return self._fetch("ex-dividend-date", {"symbol": ticker.upper()})

    def get_profile(self, ticker: str) -> Dict[str, Any]:
        """Profil společnosti (name, currency, exchange, sector, industry)."""
        rows = self._fetch("profile", {"symbol": ticker.upper()})
        return rows[0] if rows else {}


__all__ = ["FMPError", "FMPLimitExceeded", "FMPCacheClient", "DAILY_LIMIT", "get_api_key"]
