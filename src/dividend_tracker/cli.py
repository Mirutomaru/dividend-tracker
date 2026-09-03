"""CLI vstupní bod — ``divtrack analyze TICKER…`` / ``divtrack gui``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from .data.fetcher import DataFetchError
from .pipeline import analyze_ticker
from .presentation.console import format_summary_table, format_ticker_block
from .presentation.report import export_json

log = logging.getLogger(__name__)


def _preprocess(argv: Optional[List[str]]) -> List[str]:
    """Umožní zkratku `divtrack AAPL KO` bez explicitního podpříkazu analyze."""
    if argv and argv[0] not in {"analyze", "gui", "web"} and not argv[0].startswith("-"):
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
