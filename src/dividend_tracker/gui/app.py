"""Tkinter GUI — jednoduchá aplikace pro sledování dividendových akcií.

Vlevo: seznam tickerů (ruční výběr, přidávání/odstraňování). Vpravo: srovnávací
tabulka + detailní report vybraného tickeru. Analýza běží v pozadí vlákne — UI se
nemrzí. Výsledky se do UI propisují přes queue (Tk není thread-safe).
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from typing import Dict, List

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..data.fetcher import DataFetchError
from ..pipeline import analyze_ticker
from ..presentation.console import format_summary_table, format_ticker_block
from ..presentation.report import export_json

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\^=-]{0,9}$")


class DividendTrackerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Dividend Tracker — sledování a valuace dividendových akcií")
        root.geometry("1280x780")
        root.minsize(1000, 640)

        self.reports: Dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self._busy = False

        self._build_ui()
        root.after(120, self._poll_queue)

    # ------------------------------ UI konstrukce ---------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # horní lišta — výběr tickeru
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="Ticker:").pack(side=tk.LEFT)
        self.ticker_entry = ttk.Entry(top, width=14)
        self.ticker_entry.pack(side=tk.LEFT, padx=(6, 6))
        self.ticker_entry.bind("<Return>", lambda _e: self._add_ticker())
        self.btn_add = ttk.Button(top, text="Přidat a analyzovat", command=self._add_ticker)
        self.btn_add.pack(side=tk.LEFT, padx=4)
        self.btn_analyze_all = ttk.Button(top, text="Analyzovat vše znovu", command=self._analyze_all)
        self.btn_analyze_all.pack(side=tk.LEFT, padx=4)
        self.btn_remove = ttk.Button(top, text="Odebrat vybraný", command=self._remove_selected)
        self.btn_remove.pack(side=tk.LEFT, padx=4)
        self.btn_export = ttk.Button(top, text="Export JSON…", command=self._export_json)
        self.btn_export.pack(side=tk.RIGHT)

        # střed — panely
        mid = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        levý = ttk.Frame(mid)
        mid.add(levý, weight=1)
        ttk.Label(levý, text="Sledované tickery", font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(4, 2))
        self.listbox = tk.Listbox(levý, selectmode=tk.EXTENDED, activestyle="dotbox")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb_sb = ttk.Scrollbar(levý, orient=tk.VERTICAL, command=self.listbox.yview)
        lb_sb.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=lb_sb.set)

        pravý = ttk.Frame(mid)
        mid.add(pravý, weight=3)
        pv = ttk.PanedWindow(pravý, orient=tk.VERTICAL)
        pv.pack(fill=tk.BOTH, expand=True)

        # srovnávací tabulka
        tframe = ttk.Frame(pv)
        pv.add(tframe, weight=2)
        self.tree = ttk.Treeview(
            tframe, show="headings", selectmode="browse",
            columns=("ticker", "cena", "yield", "fcf_payout", "score", "safety", "zona", "signal"),
        )
        cols = {
            "ticker": ("TICKER", 80), "cena": ("CENA", 90), "yield": ("YIELD %", 90),
            "fcf_payout": ("FCF PAYOUT %", 110), "score": ("SKÓRE /100", 100),
            "safety": ("SAFETY", 80), "zona": ("NÁKUPNÍ ZÓNA", 150), "signal": ("SIGNÁL", 260),
        }
        for key, (label, width) in cols.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=tk.W if key in ("ticker", "signal") else tk.E)
        tv_sb = ttk.Scrollbar(tframe, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tv_sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tv_sb.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # detailní report
        dframe = ttk.Frame(pv)
        pv.add(dframe, weight=3)
        self.detail = tk.Text(dframe, wrap="word", state=tk.DISABLED, font=("TkFixedFont", 10))
        dt_sb = ttk.Scrollbar(dframe, orient=tk.VERTICAL, command=self.detail.yview)
        self.detail.configure(yscrollcommand=dt_sb.set)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dt_sb.pack(side=tk.LEFT, fill=tk.Y)

        # spodní stavový řádek
        self.status = ttk.Label(self.root, text="Připraveno. Přidejte ticker (např. KO).", relief=tk.SUNKEN, anchor=tk.W, padding=(8, 3))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------ akce ------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _add_ticker(self) -> None:
        raw = self.ticker_entry.get().strip().upper()
        if not TICKER_RE.match(raw):
            messagebox.showerror("Neplatný ticker", f"„{raw}“ není platné ticker symbol (příkl. KO, JNJ, BRK-B).")
            return
        items = list(self.listbox.get(0, tk.END))
        if raw not in items:
            self.listbox.insert(tk.END, raw)
        self.ticker_entry.delete(0, tk.END)
        self._start_analysis([raw])

    def _remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            item = self.listbox.get(i)
            self.listbox.delete(i)
            self.reports.pop(item, None)
            for iid in self.tree.get_children():
                if self.tree.item(iid)["values"] and self.tree.item(iid)["values"][0] == item:
                    self.tree.delete(iid)
        if not self.tree.get_children():
            self._show_detail(None)

    def _analyze_all(self) -> None:
        tickers = list(self.listbox.get(0, tk.END))
        if tickers:
            self._start_analysis(tickers)

    def _export_json(self) -> None:
        if not self.reports:
            messagebox.showinfo("Export", "Zatím nejsou žádné reporty k exportu.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile="dividend_report.json",
            filetypes=[("JSON", "*.json")], title="Uložit report do JSON",
        )
        if not path:
            return
        try:
            p = export_json(list(self.reports.values()), path)
            self._set_status(f"Exportováno: {p}")
        except OSError as exc:
            messagebox.showerror("Export selhal", str(exc))

    # ------------------------------ analýza (vlákno + queue) -----------------------

    def _start_analysis(self, tickers: List[str]) -> None:
        if self._busy:
            self._set_status("Analýza už běží — počkejte prosím.")
            return
        self._busy = True
        for tk_ in tickers:
            self._tree_set_row(tk_, {"score": "…", "safety": "…"}, status="Analyzuji…")
        threading.Thread(target=self._worker, args=(tickers,), daemon=True).start()

    def _worker(self, tickers: List[str]) -> None:
        for i, tk_ in enumerate(tickers, 1):
            self.q.put(("status", f"Analyzuji {tk_} ({i}/{len(tickers)}) …"))
            try:
                rep = analyze_ticker(tk_)
                self.q.put(("result", rep))
            except DataFetchError as exc:
                log.warning("fetch %s selhal: %s", tk_, exc)
                self.q.put(("error", (tk_, str(exc))))
        self.q.put(("done", None))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "result":
                    self._on_result(payload)
                elif kind == "error":
                    tk_, msg = payload
                    self._tree_set_row(tk_, {"score": "✗", "safety": ""}, status=f"{tk_}: {msg}")
                elif kind == "done":
                    self._busy = False
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _on_result(self, rep: dict) -> None:
        tk_ = rep["ticker"]
        self.reports[tk_] = rep
        m_div, m_health = rep["metrics"]["dividend"], rep["metrics"]["health"]
        sc, fv = rep["score"], rep["fair_value"]

        safety_disp = {"green": "ZELENÁ", "yellow": "ŽLUTÁ", "red": "ČERVENÁ"}.get(sc.get("safety_flag") or "", "—")
        score_txt = f"{sc['total_0_100']:.1f}" if sc.get("applicable") and sc.get("total_0_100") is not None else "n/a"
        zone = (f"{fv['buy_zone_low']}–{fv['buy_zone_high']} {rep['currency']}"
                if fv.get("fair_value") else "—")
        signal = "; ".join(s for s in sc.get("signals_display", [])) or ""

        iid = self._tree_find(tk_)
        values = [
            tk_,
            f"{rep['price_current']:.2f}" if rep.get("price_current") is not None else "—",
            f"{m_div['yield_trailing_pct']:.2f}%" if m_div.get("yield_trailing_pct") is not None else "—",
            f"{m_div['fcf_payout_ratio_pct']:.1f}%" if m_div.get("fcf_payout_ratio_pct") is not None else "—",
            score_txt,
            safety_disp,
            zone,
            signal[:120],
        ]
        tag = {"green": "", "yellow": "warn", "red": "red"}.get(sc.get("safety_flag") or "")
        if iid:
            self.tree.item(iid, values=values, tags=(tag,) if tag else ())
        else:
            self.tree.insert("", tk.END, values=values, tags=(tag,) if tag else ())

        self._set_status(f"Dokončeno: {tk_} — skóre {score_txt}, safety {safety_disp}")
        self._show_detail(rep)

    # ------------------------------ detail -----------------------------------------

    def _tree_find(self, tk_: str):
        for iid in self.tree.get_children():
            vals = self.tree.item(iid)["values"]
            if vals and vals[0] == tk_:
                return iid
        return None

    def _tree_set_row(self, tk_: str, partial: dict, status: str = "") -> None:
        self._set_status(status or "Analyzuji…")
        existing = self._tree_find(tk_)
        if not existing:
            values = [tk_, "—", "—", "—", partial.get("score", "…"), partial.get("safety", "…"), "…", status[:120]]
            self.tree.insert("", tk.END, values=values)

    def _on_tree_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0])["values"]
        if vals and vals[0] in self.reports:
            self._show_detail(self.reports[vals[0]])

    def _show_detail(self, rep: dict | None) -> None:
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        if rep is not None:
            self.detail.insert(tk.END, format_ticker_block(rep, color=False))
        else:
            self.detail.insert(tk.END, "Vyberte ticker v tabulce pro detailní report.")
        self.detail.configure(state=tk.DISABLED)


def main() -> int:  # noqa: D103
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    app = DividendTrackerApp(root)
    app.tree.tag_configure("red", foreground="#b00020")
    app.tree.tag_configure("warn", foreground="#8a6d00")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
