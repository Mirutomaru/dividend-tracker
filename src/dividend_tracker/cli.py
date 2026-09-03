"""CLI vstupní bod — ``divtrack analyze TICKER…`` / ``divtrack gui``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from .config import load_env as _load_env

# Načti .env PŘED importem datových modulů — DEFAULT_DB_PATH a FMP_API_KEY se
# čtou z prostředí při importu, takže konfigurace musí být už v os.environ.
_load_env()

from .data.fetcher import DataFetchError  # noqa: E402
from .pipeline import analyze_ticker
from .presentation.console import format_summary_table, format_ticker_block
from .presentation.report import export_json

log = logging.getLogger(__name__)


def _preprocess(argv: Optional[List[str]]) -> List[str]:
    """Umožní zkratku `divtrack AAPL KO` bez explicitního podpříkazu analyze."""
    if argv and argv[0] not in {"analyze", "gui", "web", "sync", "watch", "config"} and not argv[0].startswith("-"):
        return ["analyze", *argv]
    return list(argv or [])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="divtrack",
        description="Dividend Tracker — tracking, skórování a valuační analýza dividendových akcií.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_an = sub.add_parser("analyze", help="analyzovat jeden nebo více tickerů")
    p_an.add_argument("tickers", nargs="+", help="ticker(y), např. KO JNJ MSFT")
    p_an.add_argument("--json", metavar="FILE", help="export reportu do JSON souboru")
    p_an.add_argument(
        "--required-return", type=float, default=8.0,
        help="požadovaná výkonnost pro Gordon model v %% (výchozí 8)",
    )
    p_an.add_argument("--no-benchmark", action="store_true", help="přeskočit odvětvový benchmark")

    sub.add_parser("gui", help="spustit Tkinter GUI aplikaci")

    p_web = sub.add_parser("web", help="spustit webové GUI (lokální HTTP server)")
    p_web.add_argument("--host", default="127.0.0.1", help="bind adresa (výchozí 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8742, help="TCP port (výchozí 8742)")

    p_sync = sub.add_parser(
        "sync",
        help="cache-first synchronizace dat do lokální DB (FMP + yfinance ceny)",
    )
    p_sync.add_argument("tickers", nargs="*", help="ticker(y); bez argumentu se syncne vše z DB")
    p_sync.add_argument("--force", action="store_true", help="ignorovat TTL a stáhnout znovu")
    p_sync.add_argument(
        "--db", metavar="PATH", default=None,
        help="cesta k databázi (výchozí ~/.cache/dividend-tracker/data.db)",
    )

    p_watch = sub.add_parser("watch", help="správa trvalé watchlisty (souborová záloha)")
    p_watch.add_argument(
        "action", choices=["add", "remove", "list", "import", "export"],
        help="add/remove tickery, list výpis, import obnova ze souboru, export záloha do souboru",
    )
    p_watch.add_argument("tickers", nargs="*", help="ticker(y) pro add/remove")
    p_watch.add_argument("--note", default=None, help="poznámka k tickeru (add)")
    p_watch.add_argument(
        "--file", metavar="PATH", default=None,
        help="cesta k záložnímu souboru (výchozí ~/.cache/dividend-tracker/watchlist.json)",
    )
    p_watch.add_argument("--db", metavar="PATH", default=None, help="cesta k databázi")

    p_cfg = sub.add_parser("config", help="zobrazit/nastavit konfiguraci (.env)")
    p_cfg.add_argument(
        "action", nargs="?", choices=["show", "set", "unset"], default="show",
        help="show (výchozí) / set KLIC=HODNOTA / unset KLIC",
    )
    p_cfg.add_argument("kv", nargs="*", help="pro set: KLIC=HODNOTA; pro unset: KLIC")

    args = parser.parse_args(_preprocess(argv if argv is not None else sys.argv[1:]))

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "gui":
        try:
            from .gui.app import main as gui_main
        except ImportError as exc:  # např. chybí python3-tk na Linuxu
            print(f"GUI nelze spustit (chybí tkinter): {exc}\nNa Debian/Ubuntu: sudo apt install python3-tk", file=sys.stderr)
            return 1
        gui_main()
        return 0

    if args.cmd == "web":
        from .web.server import main as web_main
        try:
            web_main(host=args.host, port=args.port)
        except OSError as exc:
            print(f"Web server nelze spustit: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "sync":
        from .data.db import DEFAULT_DB_PATH, Database
        from .data.sync import sync_all

        db = Database(args.db or DEFAULT_DB_PATH)
        try:
            tickers = [t.strip().upper() for t in (args.tickers or [])]
            if not tickers:  # bez argumentu — vše, co je už v DB
                tickers = sorted(s["ticker"] for s in db.list_stocks())
            if not tickers:
                print("Databáze je prázdná — zadejte tickery, např. divtrack sync KO JNJ", file=sys.stderr)
                return 2

            logging.getLogger("dividend_tracker.data").setLevel(logging.INFO)
            print(f"Synchronizuji {len(tickers)} ticker(ů): {', '.join(tickers)}")
            summary = sync_all(db, tickers, force=args.force)

            counts = summary["counts"]
            parts = [f"{k}: {v}" for k, v in sorted(counts.items()) if v]
            print(f"Hotovo — {', '.join(parts) or 'bez změn'}")
            usage = summary.get("fmp_usage") or {}
            if usage.get("daily_limit"):
                print(
                    f"FMP: {usage['calls_used']}/{usage['daily_limit']} volání dnes "
                    f"(zbývá {usage.get('remaining')})"
                )
            quotes = summary.get("quotes") or {}
            if quotes:
                print(f"Ceny aktualizovány: {', '.join(sorted(quotes))}")
        finally:
            db.close()
        return 0

    if args.cmd == "watch":
        from .data.db import DEFAULT_DB_PATH, Database
        from .data.watchlist_store import (
            DEFAULT_WATCHLIST_FILE,
            export_watchlist_file,
            import_watchlist_file,
        )

        db = Database(args.db or DEFAULT_DB_PATH)
        file_path = Path(args.file).expanduser() if args.file else DEFAULT_WATCHLIST_FILE
        try:
            tickers = [t.strip().upper() for t in (args.tickers or [])]
            if args.action == "add":
                if not tickers:
                    print("Zadejte alespoň jeden ticker, např. divtrack watch add KO JNJ", file=sys.stderr)
                    return 2
                for tk in tickers:
                    db.upsert_watchlist(tk, note=args.note)
                export_watchlist_file(db, file_path)
                print(f"Přidáno do watchlisty: {', '.join(tickers)}")
                print(f"Záloha uložena do: {file_path}")
            elif args.action == "remove":
                if not tickers:
                    print("Zadejte alespoň jeden ticker, např. divtrack watch remove KO", file=sys.stderr)
                    return 2
                for tk in tickers:
                    db.remove_from_watchlist(tk)
                export_watchlist_file(db, file_path)
                print(f"Odebráno z watchlisty: {', '.join(tickers)}")
            elif args.action == "list":
                rows = db.list_watchlist()
                if not rows:
                    print("Watchlista je prázdná.")
                else:
                    for r in rows:
                        note = f"  — {r['note']}" if r.get("note") else ""
                        print(f"{r['ticker']}{note}")
                    print(f"\nCelkem: {len(rows)} ticker(ů)")
            elif args.action == "import":
                added = import_watchlist_file(db, file_path)
                export_watchlist_file(db, file_path)  # sjednotit soubor se stavem DB
                print(f"Obnoveno ze souboru: {added} nového tickeru (celkem {len(db.list_watchlist())})")
            elif args.action == "export":
                path = export_watchlist_file(db, file_path)
                print(f"Záloha uložena do: {path}")
        finally:
            db.close()
        return 0

    if args.cmd == "config":
        from .config import (
            DEFAULT_ENV_FILE,
            find_env_files,
            mask_secret,
            remove_env_value,
            write_env_value,
        )
        from .data.db import DEFAULT_DB_PATH

        action = args.action or "show"
        if action == "show":
            files = find_env_files()
            print(f".env soubory: {', '.join(str(p) for p in files) or '(žádný — výchozí ' + str(DEFAULT_ENV_FILE) + ')'}")
            key = os.environ.get("FMP_API_KEY", "").strip()
            print(f"FMP_API_KEY:  {mask_secret(key)}")
            print(f"DB:           {os.environ.get('DIVTRACK_DB', DEFAULT_DB_PATH)}")
            from .data.watchlist_store import DEFAULT_WATCHLIST_FILE

            print(f"Watchlist:    {DEFAULT_WATCHLIST_FILE}")
        elif action == "set":
            if len(args.kv) != 1 or "=" not in args.kv[0]:
                print("Použití: divtrack config set KLIC=HODNOTA", file=sys.stderr)
                return 2
            k, _, v = args.kv[0].partition("=")
            allowed = {"FMP_API_KEY", "DIVTRACK_DB", "DIVTRACK_WATCHLIST"}
            if k.strip() not in allowed:
                print(f"Neznámý klíč '{k}'. Povolené: {', '.join(sorted(allowed))}", file=sys.stderr)
                return 2
            path = write_env_value(k.strip(), v.strip())
            os.environ[k.strip()] = v.strip()
            shown = mask_secret(v) if k == "FMP_API_KEY" else v
            print(f"Nastaveno {k}={shown} → {path}")
        elif action == "unset":
            if len(args.kv) != 1:
                print("Použití: divtrack config unset KLIC", file=sys.stderr)
                return 2
            k = args.kv[0].strip()
            if remove_env_value(k):
                os.environ.pop(k, None)
                print(f"Odebráno {k} z .env")
            else:
                print(f"{k} nebyl v .env nalezen.")
        return 0

    if args.cmd != "analyze":
        parser.print_help()
        return 1

    tickers = [t.strip().upper() for t in args.tickers]
    reports: list[dict] = []
    n_errors = 0
    for i, tk in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] Analyzuji {tk} …")
        try:
            rep = analyze_ticker(tk, required_return_pct=args.required_return, use_benchmark=not args.no_benchmark)
            reports.append(rep)
        except DataFetchError as exc:
            n_errors += 1
            print(f"  ✗ Nedostupná data: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — neočekánaná chyba nesmí zastavit zbývající tickery
            log.exception("neočekánaná chyba při analýze %s", tk)
            n_errors += 1
            print(f"  ✗ Neočekánaná chyba: {exc}", file=sys.stderr)

    if reports:
        print()
        print(format_summary_table(reports))
        for rep in reports:
            print("\n" + "=" * 78)
            print(format_ticker_block(rep))
    else:
        print("Žádný ticker se nepodařilo analyzovat.", file=sys.stderr)

    if args.json and reports:
        path = export_json(reports, args.json)
        print(f"\nJSON report uložen do: {path}")

    return 0 if reports else 2


if __name__ == "__main__":
    raise SystemExit(main())
