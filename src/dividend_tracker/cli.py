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
    if argv and argv[0] not in {"analyze", "gui", "web", "sync", "watch", "config", "portfolio", "alert", "export"} and not argv[0].startswith("-"):
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
        "--backfill", action="store_true",
        help="doplnit historii cen za 1 rok (pro 52tídní percentil/drawdown)",
    )
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

    p_pf = sub.add_parser("portfolio", help="správa držených pozic a cíle ročního příjmů")
    p_pf.add_argument(
        "action", choices=["add", "remove", "list", "target", "tax"],
        help="add/remove pozici, list výpis + příjem, target nastavení/zobrazení cíle, tax sazby daně (list/set/remove)",
    )
    p_pf.add_argument("ticker", nargs="?", help="ticker (add/remove); pro tax: list / set / remove")
    p_pf.add_argument("--shares", type=float, default=None, help="počet akcií (add; 0 smaže pozici)")
    p_pf.add_argument("--cost", type=float, default=None, help="průměrná nákupní cena (add)")
    p_pf.add_argument("--currency", default="USD", help="měna pozice (výchozí USD)")
    p_pf.add_argument(
        "--country", default=None,
        help="země pro tax set/remove (ISO-2 kód nebo plný název, např. US)",
    )
    p_pf.add_argument(
        "--amount", type=float, default=None,
        help="pro target: cílový roční příjem; bez hodnoty se jen zobrazí aktuální stav",
    )
    p_pf.add_argument("--yield-pct", type=float, default=None, help="očekávaný yield %% pro výpočet kapitálu")
    p_pf.add_argument("--db", metavar="PATH", default=None, help="cesta k databázi")

    p_al = sub.add_parser("alert", help="upozornění na cenu / nákupní zónu (kontrola proti cenám v DB)")
    p_al.add_argument(
        "action", choices=["add", "list", "remove", "check"],
        help="add pravidlo, list výpis, remove smazat, check vyhodnotit nyní",
    )
    p_al.add_argument("ticker", nargs="?", help="ticker (add/remove)")
    p_al.add_argument(
        "--condition", choices=["below", "above", "buy_zone"], default=None,
        help="podmínka: below/above prahová cena, buy_zone = férová cena podle výnosu (výchozí below)",
    )
    p_al.add_argument("--threshold", type=float, default=None, help="prahová cena pro below/above")
    p_al.add_argument("--id", dest="alert_id", type=int, default=None, help="ID upozornění (remove)")
    p_al.add_argument("--db", metavar="PATH", default=None, help="cesta k databázi")

    p_ex = sub.add_parser("export", help="CSV export — watchlist z DB nebo hloubková analýza")
    p_ex.add_argument(
        "action", choices=["watchlist", "analysis"],
        help="watchlist = srovnávací řádky přímo z DB (offline); analysis = hloubkové reporty tickerů",
    )
    p_ex.add_argument("tickers", nargs="*", help="pro analysis: ticker(y) k analýze")
    p_ex.add_argument("--file", metavar="PATH", default=None, help="výstupní CSV soubor (výchozí watchlist.csv / analysis.csv)")
    p_ex.add_argument("--db", metavar="PATH", default=None, help="cesta k databázi")

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

            if args.backfill:
                from .data.quotes import backfill_price_history

                print("Doplňuji historii cen za 1 rok (52tídní percentil/drawdown)…")
                bf = backfill_price_history(db, tickers)
                print(f"Historie cen: {bf['points']} bodů pro {bf['tickers_with_data']} ticker(ů)")

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
                print(
                    f"Ceny aktualizovány: {quotes.get('updated', 0)} ticker(ů)"
                    + (f", selhaly: {quotes['failed']}" if quotes.get("failed") else "")
                )
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

    if args.cmd == "portfolio":
        from .data.db import DEFAULT_DB_PATH, Database
        from .data.portfolio import build_income_target_report, build_portfolio_report

        db = Database(args.db or DEFAULT_DB_PATH)
        try:
            action = args.action
            if action in ("add", "remove"):
                if not args.ticker:
                    print(f"Zadejte ticker, např. divtrack portfolio {action} KO --shares 10 --cost 65", file=sys.stderr)
                    return 2
                tk = args.ticker.strip().upper()
                if action == "add":
                    if args.shares is None:
                        print("Zadejte počet akcií: --shares N", file=sys.stderr)
                        return 2
                    db.upsert_position(tk, shares=args.shares, avg_cost=args.cost, currency=args.currency)
                    # Pozice automaticky patří do trvalé watchlisty.
                    from .data.watchlist_store import auto_add_to_watchlist

                    auto_add_to_watchlist(db, [tk])
                    print(f"Pozice {tk}: {args.shares} ks" + (f" @ {args.cost}" if args.cost else ""))
                else:
                    db.remove_position(tk)
                    print(f"Pozice {tk} odstraněna.")
            elif action == "list":
                report = build_portfolio_report(db)
                if not report["positions"]:
                    print("Portfolio je prázdné — přidejte pozici: divtrack portfolio add KO --shares 10 --cost 65")
                else:
                    for p in report["positions"]:
                        price_s = f"{p['price']:.2f}" if p["price"] is not None else "—"
                        mv_s = f"{p['market_value']:,.2f}" if p["market_value"] is not None else "—"
                        inc_s = f"{p['annual_income']:,.2f} {p['currency']}/rok" if p["dps_ttm"] else "bez DPS v DB"
                        print(f"{p['ticker']:<6} {p['shares']:>8.2f} ks  cena {price_s:>9}  hodnota {mv_s:>12}  příjem {inc_s}")
                    mv = report["total_market_value"]
                    inc = report["annual_income"]
                    yld = report["portfolio_yield_pct"]
                    print("-" * 78)
                    print(f"Hodnota: {mv:,.2f}" if mv else "Hodnota: — (chybí ceny)")
                    print(
                        f"Roční příjem: {inc:,.2f}  |  portfolio yield: {yld}%  |  pozic: {report['n_positions']}"
                        if yld is not None
                        else f"Roční příjem: {inc:,.2f}  |  pozic: {report['n_positions']}"
                    )
                target = build_income_target_report(db)
                if target.get("configured"):
                    print(
                        f"Cíl: {target['current_annual_income']:,.0f}/{target['target_annual_income']:,.0f} "
                        f"({target['progress_pct']}%) — chybí {target['remaining_income']:,.0f}/rok, "
                        f"potřebný kapitál ~{target['required_capital']:,.0f}"
                    )
            elif action == "target":
                if args.amount is not None:
                    db.set_setting("income_target", str(args.amount))
                    print(f"Cíl ročního příjmů nastaven na {args.amount:,.0f}")
                if args.yield_pct is not None:
                    db.set_setting("target_yield_pct", str(args.yield_pct))
                    print(f"Očekávaný yield pro výpočet kapitálu: {args.yield_pct}%")
                target = build_income_target_report(db)
                if not target.get("configured"):
                    print("Cíl není nastaven — použijte: divtrack portfolio target --amount 60000")
                else:
                    print(
                        f"Cíl: {target['current_annual_income']:,.0f}/{target['target_annual_income']:,.0f} "
                        f"({target['progress_pct']}%)"
                    )
                    if target.get("required_capital"):
                        print(f"Potřebný kapitál (yield {target['effective_yield_pct_used']}%): ~{target['required_capital']:,.0f}")
            elif action == "tax":
                from .domain.tax import WITHHOLDING_RATES, _normalize_country

                sub_action = args.ticker  # list / set / remove
                if sub_action is None:
                    print("Použití: divtrack portfolio tax list | set --country US <sazba> | remove --country US", file=sys.stderr)
                    return 2
                overrides = {k: v for k, v in db.list_settings().items() if k.startswith("withholding_")}

                if sub_action == "list":
                    print("Sazby zadržovací daně (pro rezidenta ČR):")
                    codes = sorted(set(WITHHOLDING_RATES) | {k.split("_", 1)[1] for k in overrides})
                    for code in codes:
                        built_in = WITHHOLDING_RATES.get(code)
                        ov = overrides.get(f"withholding_{code}")
                        src = "přepsáno v DB" if ov is not None else ("vestavěná" if built_in is not None else "—")
                        val = ov if ov is not None else (f"{built_in:g}" if built_in is not None else "?")
                        print(f"  {code:<4} {val:>6}%   ({src})")
                    print("\nPozn.: sazby jsou odhad — pro přesné daňové poradenství se poraďte s daňovým poradcem.")

                elif sub_action == "set":
                    if not args.country:
                        print("Zadejte zemi: --country US", file=sys.stderr)
                        return 2
                    code = _normalize_country(args.country)
                    if not code:
                        print(f"Nerozpoznaná země: {args.country}", file=sys.stderr)
                        return 2
                    rate = args.amount
                    if rate is None or not (0.0 <= rate <= 100.0):
                        print("Zadejte sazbu v rozmezí 0–100: divtrack portfolio tax set --country US 15", file=sys.stderr)
                        return 2
                    db.set_setting(f"withholding_{code}", str(rate))
                    print(f"Sazba pro {code} nastavena na {rate:g}% (přepsání vestavěné sazby).")

                elif sub_action == "remove":
                    if not args.country:
                        print("Zadejte zemi: --country US", file=sys.stderr)
                        return 2
                    code = _normalize_country(args.country)
                    if not code:
                        print(f"Nerozpoznaná země: {args.country}", file=sys.stderr)
                        return 2
                    db.delete_setting(f"withholding_{code}")
                    built_in = WITHHOLDING_RATES.get(code)
                    print(
                        f"Přepsání sazby pro {code} odstraněno — vrací se vestavěná sazba "
                        + (f"{built_in:g}%." if built_in is not None else "(žádná).")
                    )

                else:
                    print(f"Neznámá akce tax: {sub_action} (použijte list / set / remove)", file=sys.stderr)
                    return 2
        finally:
            db.close()
        return 0

    if args.cmd == "alert":
        from .data.alerts import add_alert, evaluate_alerts
        from .data.db import DEFAULT_DB_PATH, Database

        db = Database(args.db or DEFAULT_DB_PATH)
        try:
            action = args.action
            if action == "add":
                if not args.ticker:
                    print("Zadejte ticker, např. divtrack alert add KO --condition below --threshold 65", file=sys.stderr)
                    return 2
                condition = args.condition or "below"
                try:
                    aid = add_alert(db, args.ticker.strip().upper(), condition, args.threshold)
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
                cond_desc = {
                    "below": f"pod {args.threshold}",
                    "above": f"nad {args.threshold}",
                    "buy_zone": "v nákupní zóně (férová cena)",
                }[condition]
                print(f"Přidáno upozornění #{aid}: {args.ticker.strip().upper()} — {cond_desc}")
            elif action == "list":
                rows = db.list_alerts(active_only=False)
                if not rows:
                    print("Žádná upozornění.")
                else:
                    for r in rows:
                        thr = f" @ {r['threshold']}" if r.get("threshold") is not None else ""
                        state = "aktivní" if r["active"] else "vypnuté"
                        last = f", naposledy: {r['last_triggered']}" if r.get("last_triggered") else ""
                        print(f"#{r['id']}  {r['ticker']:<6} {r['condition']}{thr}  ({state}{last})")
                    print(f"\nCelkem: {len(rows)} upozornění")
            elif action == "remove":
                if args.alert_id is None:
                    print("Zadejte ID: divtrack alert remove --id N (výpis: divtrack alert list)", file=sys.stderr)
                    return 2
                db.remove_alert(args.alert_id)
                print(f"Upozornění #{args.alert_id} odstraněno.")
            elif action == "check":
                triggered = evaluate_alerts(db)
                if not triggered:
                    print("Žádné upozornění nebylo vyhodnoceno (ceny v DB, max. 1× denně na pravidlo).")
                else:
                    for t in triggered:
                        print(t["message"])
        finally:
            db.close()
        return 0

    if args.cmd == "export":
        from .data.db import DEFAULT_DB_PATH, Database
        from .presentation.csv_export import export_analysis_csv, export_watchlist_csv

        out = Path(args.file).expanduser() if args.file else (
            Path("watchlist.csv") if args.action == "watchlist" else Path("analysis.csv")
        )
        if args.action == "watchlist":
            db = Database(args.db or DEFAULT_DB_PATH)
            try:
                from .data.watchlist import build_watchlist_rows

                rows = build_watchlist_rows(db)
                if not rows:
                    print("Watchlista je prázdná — nejdřív spusťte divtrack sync KO JNJ …", file=sys.stderr)
                    return 2
                path = export_watchlist_csv(rows, out)
                print(f"Exportováno {len(rows)} řádků do: {path}")
            finally:
                db.close()
        else:  # analysis — hloubková analýza (vyžaduje externí data)
            tickers = [t.strip().upper() for t in args.tickers]
            if not tickers:
                print("Zadejte tickery, např. divtrack export analysis KO JNJ", file=sys.stderr)
                return 2
            reports: list[dict] = []
            n_errors = 0
            for i, tk in enumerate(tickers, 1):
                print(f"[{i}/{len(tickers)}] Analyzuji {tk} …")
                try:
                    reports.append(analyze_ticker(tk))
                except DataFetchError as exc:
                    n_errors += 1
                    print(f"  ✗ Nedostupná data: {exc}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001 — chyba nesmí zastavit zbývající tickery
                    log.exception("neočekávaná chyba při analýze %s", tk)
                    n_errors += 1
                    print(f"  ✗ Neočekávaná chyba: {exc}", file=sys.stderr)
            if not reports:
                print("Žádný ticker se nepodařilo analyzovat.", file=sys.stderr)
                return 2
            path = export_analysis_csv(reports, out)
            print(f"Exportováno {len(reports)} report(ů) do: {path}" + (f" ({n_errors} selhalo)" if n_errors else ""))
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
