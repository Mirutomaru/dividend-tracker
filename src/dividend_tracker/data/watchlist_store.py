"""Perzistentní watchlist — trvalý soubor, aby seznam akcií nebyl nikdy ztracen.

Dvojí záloha:
1. **SQLite tabulka ``watchlist``** (primární úložiště v ``data.db``).
2. **Soubor JSON** (výchozí ``~/.config/dividend-tracker/watchlist.json``,
   překrytelné přes ``DIVTRACK_WATCHLIST``) — přepisuje se po každé změně
   watchlisty atomicky (tmp + rename). Pokud by se DB soubor smazal nebo
   poškodil, celý seznam se obnoví příkazem ``divtrack watch import``.

Soubor je záměrně lidsky čitelný a editovatelný::

    {
      "updated_at": 1756900000.0,
      "tickers": [
        {"ticker": "KO", "note": "kralovna dividend", "added_at": 1756800000.0},
        ...
      ]
    }

Funkce:
- :func:`export_watchlist_file` — záloha DB → soubor (volá se po každé změně)
- :func:`import_watchlist_file` — obnova soubor → DB (idempotentní, slučuje)
- :func:`auto_add_to_watchlist` — automaticky přidá tickery do trvalé watchlisty
  při analýze/syncu, aby se seznam sám budoval a nikdy neztratil.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from .db import Database

log = logging.getLogger(__name__)

#: Výchozí cesta záložního souboru watchlisty (vedle databáze).
DEFAULT_WATCHLIST_FILE = Path(
    os.environ.get("DIVTRACK_WATCHLIST", "~/.config/dividend-tracker/watchlist.json")
).expanduser()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    """Atomický zápis (dočasný soubor + rename) — nikdy nezůstane poloviční soubor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".watchlist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        # Uklidíme dočasný soubor a propíšeme chybu dál.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def export_watchlist_file(db: Database, path: Path | str = DEFAULT_WATCHLIST_FILE) -> Path:
    """Zálohuje aktuální watchlistu z DB do souboru. Vrátí cestu k souboru."""
    p = Path(path)
    tickers = [
        {
            "ticker": r["ticker"],
            "note": r.get("note"),
            "added_at": r.get("added_at"),
        }
        for r in db.list_watchlist()
    ]
    _atomic_write(p, {"updated_at": time.time(), "tickers": tickers})
    return p


def import_watchlist_file(db: Database, path: Path | str = DEFAULT_WATCHLIST_FILE) -> int:
    """Obnoví watchlistu ze souboru do DB (slučuje — existující záznamy se nedotknou).

    Vrátí počet **přidaných** tickerů. Chybějící/nesprávný soubor → 0 (bez výjimky,
    aby noční cron nepadl na poškozené záloze).
    """
    p = Path(path)
    if not p.exists():
        log.warning("watchlist soubor neexistuje: %s", p)
        return 0
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        items: List[Dict[str, Any]] = payload.get("tickers", []) if isinstance(payload, dict) else []
    except (json.JSONDecodeError, OSError) as exc:
        log.error("watchlist soubor nelze přečíst (%s): %s", p, exc)
        return 0

    added = 0
    for item in items:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker or db.is_in_watchlist(ticker):
            continue
        db.upsert_watchlist(ticker, note=item.get("note"))
        added += 1
    return added


def auto_add_to_watchlist(db: Database, tickers: List[str]) -> int:
    """Automaticky přidá tickery do trvalé watchlisty (idempotentní).

    Volá se při analýze i syncu — seznam se tím sám budoval a po každé změně
    se zálohuje do souboru. Vrátí počet nově přidaných tickerů.
    """
    added = 0
    for tk in tickers:
        tk = tk.strip().upper()
        if not tk or db.is_in_watchlist(tk):
            continue
        db.upsert_watchlist(tk)
        added += 1
    if added:
        try:
            # Explicitní odkaz na modulovou konstantu (ne default argument),
            # aby testy mohly cestu zálohy přesměrovat.
            export_watchlist_file(db, DEFAULT_WATCHLIST_FILE)
        except OSError as exc:
            log.warning("záloha watchlisty se nepodařila: %s", exc)
    return added


__all__ = [
    "DEFAULT_WATCHLIST_FILE",
    "auto_add_to_watchlist",
    "export_watchlist_file",
    "import_watchlist_file",
]
