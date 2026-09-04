"""Konzolový formát reportů — čisté textové bloky (bez ANSI, pokud není TTY)."""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

SAFETY_DISPLAY = {"green": "ZELENÁ", "yellow": "ŽLUTÁ", "red": "ČERVENÁ"}


def _c(color: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    codes = {"g": 32, "y": 33, "r": 31, "b": 36, "dim": 2}
    return f"\033[{codes.get(color, 0)}m{s}\033[0m"


def fmt(v: Optional[float], nd: int = 2, suffix: str = "", dash: str = "—") -> str:
    if v is None:
        return dash
    s = f"{v:.{nd}f}"
    return f"{s}{suffix}"


def _flag_color(flag: Optional[str]) -> str:
    return {"green": "g", "yellow": "y", "red": "r"}.get(flag or "", "")


def format_ticker_block(report: Dict[str, Any], color: bool = True) -> str:
    """Detailní blok jednoho tickeru (používá i GUI detail pane)."""
    m = report["metrics"]
    div, val, health = m["dividend"], m["valuation"], m["health"]
    score = report["score"]
    fv = report["fair_value"]
    cur = report.get("currency", "USD")

    lines: List[str] = []
    name = (report["company"].get("name") or "").strip()
    sector = (report["company"].get("sector") or "")
    head = f" {report['ticker']} — {name}" if name else f" {report['ticker']}"
    lines.append(f"{head}  [{sector}]")
    lines.append(f"Cena: {fmt(report.get('price_current'), 2, ' ' + cur)}")

    def sec(title: str) -> None:
        lines.append("")
        lines.append(f"── {title} " + "─" * max(0, 58 - len(title)))

    if score.get("applicable"):
        flag = score.get("safety_flag") or ""
        total = fmt(score.get("total_0_100"), 1)
        lines.append("")
        col = _flag_color(flag) if color else ""
        lines.append(
            f"SKÓRE DIVIDENDOVÉHO ZDRAVÍ: {(_c(col, total + ' / 100') if color else total)}   "
            f"Safety flag: {(_c(col, SAFETY_DISPLAY.get(flag, flag.upper())) if color else SAFETY_DISPLAY.get(flag, flag))}"
        )
        bd = score.get("breakdown") or {}
        lines.append(
            f"  bloky: udržitelnost {fmt(bd.get('sustainability_pct'),1)} · "
            f"valuace {fmt(bd.get('valuation_pct'),1)} · kvalita/růst {fmt(bd.get('quality_growth_pct'),1)}"
        )
        for sig in score.get("signals_display", []):
            lines.append(f"  → signál: {_c('r' if 'PAST' in sig else 'g', sig) if color else sig}")

    sec("A. Dividendy & udržitelnost")
    lines.append(
        f"  Yield (trailing / forward): {fmt(div.get('yield_trailing_pct'),2,'%')} / "
        f"{fmt(div.get('yield_forward_pct'),2,'%')}   DPS TTM: {fmt(div.get('dps_ttm'),4, ' '+cur)}"
    )
    lines.append(
        f"  Payout (EPS): {fmt(div.get('payout_ratio_eps_pct'),1,'%')}   "
        f"FCF payout: {fmt(div.get('fcf_payout_ratio_pct'),1,'%')}"
    )
    if div.get("ffo_payout_ratio_pct") is not None or div.get("affo_payout_ratio_pct") is not None:
        lines.append(
            f"  REIT payout (FFO / AFFO): {fmt(div.get('ffo_payout_ratio_pct'),1,'%')} / "
            f"{fmt(div.get('affo_payout_ratio_pct'),1,'%')}"
        )
    lines.append(
        f"  Růst dividend CAGR (3Y/5Y/10Y): {fmt(div.get('growth_cagr_3y_pct'),2,'%')} / "
        f"{fmt(div.get('growth_cagr_5y_pct'),2,'%')} / {fmt(div.get('growth_cagr_10y_pct'),2,'%')}"
    )
    lines.append(f"  Streak růstu výplat: {div.get('dividend_streak_years', 0)} let")

    sec("B. Ocenění & valuace (vs odvětví)")
    bench = report.get("benchmark_sector") or {}
    own5y = val.get("own_5y_median_pe_approx")
    lines.append(
        f"  P/E TTM: {fmt(val.get('pe_ttm'))}  (odv. medián {fmt(bench.get('median_pe_ttm'))}, "
        f"vlastní 5Y aprox. {fmt(own5y)})   P/E forward: {fmt(val.get('pe_forward'))}"
    )
    lines.append(
        f"  P/FCF: {fmt(val.get('p_fcf'))}   EV/EBITDA: {fmt(val.get('ev_ebitda'))} "
        f"(odv. medián {fmt(bench.get('median_ev_ebitda'))})   FCF yield: {fmt(val.get('fcf_yield_pct'),2,'%')}"
    )
    lines.append(
        f"  Dividendový výnos vs 5Y průměr: {fmt(div.get('yield_trailing_pct'),2,'%')} vs "
        f"{fmt(val.get('dividend_yield_5y_avg_pct'),2,'%')} "
        f"(odchylka {fmt(val.get('yield_vs_5y_avg_deviation_pct'),1,' %')})"
    )

    sec("C. Finanční zdraví & solvence")
    lines.append(
        f"  Net Debt/EBITDA: {fmt(health.get('net_debt_to_ebitda'))}x   "
        f"Interest Coverage: {fmt(health.get('interest_coverage'))}x   ROIC: {fmt(health.get('roic_pct'),1,'%')}"
    )

    sec("Férová cena & nákupní zóna")
    if fv.get("ddm_model") == "two_stage":
        ddm_line = (
            f"  Two-stage DDM (r={fv.get('required_return_pct',8):.0f} %, g1≈{fmt(fv.get('growth_assumed_pct'),1,'%')}): "
            f"{fmt(fv.get('two_stage_price'),2,' '+cur)}   Výnosová metoda: {fmt(fv.get('yield_based_price'),2,' '+cur)}"
        )
    else:
        ddm_line = (
            f"  Gordon (r={fv.get('required_return_pct',8):.0f} %, g≈{fmt(fv.get('growth_assumed_pct'),1,'%')}): "
            f"{fmt(fv.get('gordon_price'),2,' '+cur)}   Výnosová metoda: {fmt(fv.get('yield_based_price'),2,' '+cur)}"
        )
    lines.append(ddm_line)
    if fv.get("fair_value"):
        lines.append(
            f"  FÉROVÁ CENA: {fv['fair_value']} {cur}   NÁKUPNÍ ZÓNA: "
            f"{fmt(fv.get('buy_zone_low'),2)} – {fmt(fv.get('buy_zone_high'),2)} {cur}"
        )
    lines.append(f"  Úsudek: {fv.get('verdict_display', '—')}")

    notes = list(report.get("notes", [])) + [f"data_gap: {g}" for g in report.get("data_gaps", [])]
    if notes:
        sec("Poznámky / chybějící data")
        lines.extend(f"  · {n}" for n in notes)

    return "\n".join(lines)


def format_summary_table(reports: List[Dict[str, Any]], color: bool = True) -> str:
    """Kompaktní srovnávací tabulka všech tickerů."""
    if not reports:
        return "(žádné reporty)"

    def row(r: Dict[str, Any]) -> List[str]:
        sc = r["score"]
        fv = r["fair_value"]
        zone = (
            f"{fv['buy_zone_low']}–{fv['buy_zone_high']}" if fv.get("fair_value") else "—"
        )
        flag = SAFETY_DISPLAY.get(sc.get("safety_flag") or "", sc.get("safety_flag") or "—")
        sigs = "; ".join(s for s in sc.get("signals_display", []) if "PAST" in s) or (
            "podhodnoceno?" if any("nákupní zóna" in s.lower() for s in sc.get("signals_display", [])) else ""
        )
        return [
            r["ticker"],
            fmt(r.get("price_current"), 2),
            fmt((r["metrics"]["dividend"]).get("yield_trailing_pct"), 2, "%"),
            fmt((r["metrics"]["dividend"]).get("fcf_payout_ratio_pct"), 1, "%"),
            fmt((r["metrics"]["health"]).get("net_debt_to_ebitda")),
            (fmt(sc.get("total_0_100"), 1) if sc.get("applicable") else "n/a"),
            flag,
            zone,
            sigs[:28],
        ]

    headers = ["TICKER", "CENA", "YIELD", "FCF PAYOUT", "ND/EBITDA", "SKÓRE", "SAFETY", "NÁKUPNÍ ZÓNA", "SIGNÁL"]
    rows = [row(r) for r in reports]

    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    sep = "  "

    def line(cells: List[str]) -> str:
        return sep.join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out = [line(headers), line(["-" * w for w in widths])]
    for _r_rep, cells in zip(reports, rows):
        flag_color = {"ZELENÁ": "g", "ŽLUTÁ": "y", "ČERVENÁ": "r"}.get(cells[6], "")
        if color:
            cells = list(cells)
            cells[6] = _c(flag_color, cells[6])
            total_cell = cells[5]
            if total_cell not in ("n/a", "—"):
                try:
                    t = float(total_cell)
                    cells[5] = _c("g" if t >= 70 else "y" if t >= 40 else "r", total_cell)
                except ValueError:
                    pass
        out.append(line(cells))
    return "\n".join(out)
