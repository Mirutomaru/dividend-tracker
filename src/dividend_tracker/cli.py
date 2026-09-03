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


def _preprocess(argv: Optional[List[str]]) -> List[str]:
    """Umožní zkratku `divtrack AAPL KO` bez explicitního podpříkazu analyze."""
    if argv and argv[0] not in {"analyze", "gui"} and not argv[0].startswith("-"):
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

    args = parser.parse_args(_preprocess(argv))

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "gui":
        try:
            from .gui.app import main as gui_main
        except ImportError as exc:  # např. chybí python3-tk na Linuxu
            print(f"GUI nelze spustit (chybí tkinter): {exc}\nNa Debian/Ubuntu: sudo apt install python3-tk", file=sys.stderr)
            return 1
        gui_main()
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
