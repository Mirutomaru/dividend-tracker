"""Lehký webový server pro Dividend Tracker — pouze standardní knihovna.

Architektura:
- :class:`JobManager` — thread-safe správa asynchronních analýz (ThreadPoolExecutor),
  aby dlouhé volání yfinance nezavěšovalo HTTP requesty.
- :class:`WebApp` — ``http.server`` handler se statickým frontendem a malou JSON API:

  =======================  =====================================================
  GET  /                   index.html
  GET  /static/<file>      style.css, app.js (jen soubory ze static/ adresáře)
  GET  /api/health         {status: ok}
  POST /api/analyze        {tickers: [...], required_return_pct?, use_benchmark?}
                           → 202 {job_id}
  GET  /api/jobs/<id>      {status, total, done, results: [{ticker, report|error}]}
  GET  /api/watchlist      srovnávací řádky PŘÍMO Z LOKÁLNÍ DB (bez externích volání)
  POST /api/sync           {tickers: [...], force?} → 202 {job_id} — cache-first sync do DB
  POST /api/scan           {tickers?: [...]} → 202 {job_id} — scan (zelený flag + BUY_ZONE);
                           bez tickers = celá trvalá watchlista; job.extra.matches po dokončení
  GET  /api/fmp-status     stav čítače FMP denních volání (limit 250/den)
  GET  /api/reports        uložené analytické reporty z lokální DB (přežívají restart GUI)
  DELETE /api/reports/<ticker>   smaže uložený report tickera (idempotentní)
  =======================  =====================================================

Bezpečnostní poznamky: server je určen pro lokální použití (výchozí bind
127.0.0.1), tickery se validují přísným regulárním výrazem a statické cesty
se normalizují proti path traversalu.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import load_env as _load_env

# Načti .env PŘED importem datových modulů (DEFAULT_DB_PATH / FMP_API_KEY se
# čtou z prostředí při importu). CLI to už udělal, ale přímé spuštění serveru
# (python -m dividend_tracker.web.server) potřebuje vlastní načtení.
_load_env()

from ..data.db import DEFAULT_DB_PATH, Database  # noqa: E402
from ..data.fetcher import DataFetchError
from ..data.sync import sync_all
from ..data.watchlist import build_watchlist_rows
from ..domain.scoring import SIGNAL_BUY_ZONE
from ..pipeline import analyze_ticker

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Přísný formát tickera — alfanumerika + tečka a pomlčka (BRK.B, BRK-B).
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")
MAX_TICKERS_PER_JOB = 100

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@dataclass
class Job:
    """Stav jednoho analyzačního jobu (thread-safe přes :attr:`JobManager._lock`)."""

    id: str
    total: int
    status: str = "running"  # running | done
    created_at: float = field(default_factory=time.time)
    results: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)  # doplňkové pole (např. summary sync jobu)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": len(self.results),
            "results": self.results,
            **self.extra,
        }


class JobManager:
    """Spravuje asynchronní analýzy tickerů v thread poolu."""

    def __init__(self, max_workers: int = 6) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="divtrack")

    def submit(
        self,
        tickers: List[str],
        required_return_pct: float,
        use_benchmark: bool,
        db: Optional[Database] = None,
    ) -> Job:
        """Spustí analýzu; s ``db`` se každý hotový report perzistuje do lokální DB."""
        job = Job(id=uuid.uuid4().hex[:12], total=len(tickers))
        with self._lock:
            self._jobs[job.id] = job
        for ticker in tickers:
            self._executor.submit(self._run_one, job, ticker, required_return_pct, use_benchmark, db)
        return job

    def submit_sync(self, db: Database, tickers: List[str], force: bool) -> Job:
        """Asynchronní cache-first synchronizace dat do DB (fundamenty + ceny)."""
        job = Job(id=uuid.uuid4().hex[:12], total=len(tickers))
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run_sync, job, db, tickers, force)
        return job

    def _run_sync(self, job: Job, db: Database, tickers: List[str], force: bool) -> None:
        try:
            summary = sync_all(db, tickers, force=force)
            with self._lock:
                job.extra["summary"] = summary
                job.status = "done"
        except Exception as exc:  # noqa: BLE001 — chyba nesmí zabít job
            log.exception("sync job %s selhal", job.id)
            with self._lock:
                job.extra["error"] = f"Synchronizace selhala: {exc}"
                job.status = "done"

    def submit_scan(
        self,
        tickers: List[str],
        required_return_pct: float,
        use_benchmark: bool,
        db: Optional[Database] = None,
    ) -> Job:
        """Asynchronní scan (jako CLI ``divtrack scan``): analyzuje a na konci
        vyfiltruje akce se zeleným safety flagem + signálem BUY_ZONE."""
        job = Job(id=uuid.uuid4().hex[:12], total=len(tickers))
        with self._lock:
            self._jobs[job.id] = job
        for ticker in tickers:
            self._executor.submit(self._run_one, job, ticker, required_return_pct, use_benchmark, db)
        # Filtrování až po dokončení všech analýz (results jsou hotové).
        self._executor.submit(self._finalize_scan, job)
        return job

    def _finalize_scan(self, job: Job) -> None:
        """Po dokončení scan jobu doplní ``matches`` (zelený flag + BUY_ZONE)."""
        # _run_one nastaví status="done" až po posledním výsledku — počkat na něj.
        deadline = time.time() + 3600
        while time.time() < deadline:
            with self._lock:
                finished = job.status == "done" and len(job.results) >= job.total
                if finished:
                    matches = [
                        e["ticker"]
                        for e in job.results
                        if "report" in e
                        and e["report"].get("score", {}).get("safety_flag") == "green"
                        and SIGNAL_BUY_ZONE in e["report"].get("score", {}).get("signals", [])
                    ]
                    job.extra["matches"] = matches
            if finished:
                return
            time.sleep(0.1)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run_one(
        self,
        job: Job,
        ticker: str,
        required_return_pct: float,
        use_benchmark: bool,
        db: Optional[Database] = None,
    ) -> None:
        try:
            report = analyze_ticker(ticker, required_return_pct=required_return_pct, use_benchmark=use_benchmark)
            entry: Dict[str, Any] = {"ticker": ticker, "report": report}
        except DataFetchError as exc:
            log.warning("job %s: data nedostupná pro %s: %s", job.id, ticker, exc)
            entry = {"ticker": ticker, "error": f"Nedostupná data: {exc}"}
        except Exception as exc:  # noqa: BLE001 — chyba nesmí zabít celý job
            log.exception("job %s: neočekávaná chyba pro %s", job.id, ticker)
            entry = {"ticker": ticker, "error": f"Neočekávaná chyba: {exc}"}
        with self._lock:
            job.results.append(entry)
            if len(job.results) >= job.total:
                job.status = "done"
        # Perzistence hotového reportu — web GUI pak ukáže kompletní přehled
        # všech analyzovaných akcií i po obnovení stránky. Chyba zápisu nesmí
        # ovlivnit výsledek jobu (report už je v ``entry``).
        if db is not None and "report" in entry:
            try:
                db.save_report(entry["report"])
            except Exception as exc:  # noqa: BLE001 — DB chyba nesmí zabít job
                log.warning("job %s: report pro %s se nepodařilo uložit do DB: %s", job.id, ticker, exc)


class WebApp(BaseHTTPRequestHandler):
    """HTTP handler — statické soubory + JSON API."""

    server_version = "DividendTracker/0.1"
    # JobManager a Database se injectují v :func:`create_app` (třídová atributová reference).
    jobs: JobManager  # type: ignore[valid-type]
    db: Database  # type: ignore[valid-type]

    # ------------------------------------------------------------------ util

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        """Přečte a parsuje JSON tělo requestu; vrací ``None`` při chybě (400 už odesláno)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send_error_json(400, "Neplatné JSON tělo.")
            return None
        if not isinstance(payload, dict):
            self._send_error_json(400, "JSON tělo musí být objekt.")
            return None
        return payload

    def _valid_ticker(self, value: Any) -> Optional[str]:
        """Normalizuje ticker na uppercase; vrací ``None`` pokud neplatný (400 odesláno)."""
        t = str(value or "").strip().upper()
        if not TICKER_RE.match(t):
            self._send_error_json(400, f"Neplatný ticker: {value!r}")
            return None
        return t

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        ctype = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_csv(self, body: bytes, filename: str) -> None:
        """Odešle CSV soubor ke stažení (Content-Disposition attachment)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ GET

    def do_GET(self) -> None:  # noqa: N802 — API http.server
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/health":
                self._send_json({"status": "ok"})
                return
            if path.startswith("/api/jobs/"):
                self._handle_job_get(path)
                return
            if path == "/api/watchlist":
                self._handle_watchlist()
                return
            if path == "/api/reports":
                self._handle_reports_get()
                return
            if path == "/api/portfolio":
                self._handle_portfolio()
                return
            if path == "/api/alerts":
                self._handle_alerts_get()
                return
            if path == "/api/fmp-status":
                from ..data.fmp import FMPCacheClient

                usage = FMPCacheClient(self.db).usage()
                self._send_json(
                    {
                        "configured": bool(os.environ.get("FMP_API_KEY", "").strip()),
                        **usage,
                    }
                )
                return
            if path == "/api/export/watchlist.csv":
                self._handle_export_watchlist_csv()
                return
            if path.startswith("/api/export/analysis/") and path.endswith(".csv"):
                job_id = path[len("/api/export/analysis/") : -len(".csv")]
                self._handle_export_analysis_csv(job_id)
                return
            if path in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "index.html")
                return
            if path.startswith("/static/"):
                self._handle_static(path)
                return
            self._send_error_json(404, "Nenalezeno.")
        except (BrokenPipeError, ConnectionResetError):
            log.debug("klient ukončil spojení")

    def _handle_watchlist(self) -> None:
        """Srovnávací řádky přímo z lokální DB — žádné externí volání."""
        rows = build_watchlist_rows(self.db)
        self._send_json({"rows": rows, "count": len(rows)})

    def _handle_reports_get(self) -> None:
        """Uložené analytické reporty přímo z lokální DB (bez externích volání)."""
        reports = self.db.list_reports()
        self._send_json({"reports": reports, "count": len(reports)})

    def _handle_portfolio(self) -> None:
        """Portfolio + cíl ročního příjmů přímo z lokální DB."""
        from ..data.portfolio import build_income_target_report, build_portfolio_report

        report = build_portfolio_report(self.db)
        target = build_income_target_report(self.db)
        self._send_json({"portfolio": report, "income_target": target})

    def _handle_export_watchlist_csv(self) -> None:
        """CSV export watchlistu přímo z lokální DB (bez externích volání)."""
        from ..presentation.csv_export import watchlist_csv_text

        rows = build_watchlist_rows(self.db)
        # utf-8-sig = BOM, aby se soubor správně otevřel v Excelu (jako file export)
        body = watchlist_csv_text(rows).encode("utf-8-sig")
        self._send_csv(body, "watchlist.csv")

    def _handle_export_analysis_csv(self, job_id: str) -> None:
        """CSV export výsledků analytického jobu (jen hotové reporty)."""
        from ..presentation.csv_export import analysis_csv_text

        job = self.jobs.get(job_id)
        if job is None:
            self._send_error_json(404, f"Job {job_id!r} nebyl nalezen.")
            return
        reports = [e["report"] for e in job.results if "report" in e]
        if not reports:
            self._send_error_json(404, "Job nemá žádné hotové reporty k exportu.")
            return
        body = analysis_csv_text(reports).encode("utf-8-sig")  # BOM pro Excel
        safe_id = "".join(c for c in job_id if c.isalnum() or c in "-_")[:32]
        self._send_csv(body, f"analysis_{safe_id}.csv")


    def _handle_alerts_get(self) -> None:
        """Seznam upozornění + aktuální ceny pro kontext (vše z lokální DB)."""
        rows = []
        for a in self.db.list_alerts(active_only=False):
            quote = self.db.get_quote(a["ticker"]) or {}
            price = quote.get("current_price")
            rows.append({**a, "price": float(price) if price else None})
        self._send_json({"alerts": rows, "count": len(rows)})

    def _handle_alerts_post(self, payload: dict) -> None:
        """Přidá upozornění (ticker + condition [+ threshold])."""
        from ..data.alerts import VALID_CONDITIONS, add_alert

        ticker = str(payload.get("ticker") or "").strip().upper()
        condition = payload.get("condition") or "below"
        if not ticker:
            self._send_error_json(400, "Chybí 'ticker'.")
            return
        if not TICKER_RE.match(ticker):
            self._send_error_json(400, f"Neplatný ticker: {payload.get('ticker')!r}")
            return
        if condition not in VALID_CONDITIONS:
            self._send_error_json(400, f"Neplatná podmínka {condition!r}. Povolené: {', '.join(VALID_CONDITIONS)}")
            return
        threshold = payload.get("threshold")
        try:
            threshold_f = float(threshold) if threshold is not None else None
            aid = add_alert(self.db, ticker, condition, threshold_f)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        alert = next((a for a in self.db.list_alerts(active_only=False) if a["id"] == aid), None)
        self._send_json({"ok": True, "alert": alert}, status=201)

    def _handle_job_get(self, path: str) -> None:
        job_id = path.rsplit("/", 1)[-1]
        job = self.jobs.get(job_id)
        if job is None:
            self._send_error_json(404, "Job nenalezen.")
            return
        self._send_json(job.to_dict())

    def _handle_static(self, path: str) -> None:
        rel = path[len("/static/"):]
        target = (STATIC_DIR / rel).resolve()
        # Path traversal ochrana — cíl musí zůstat uvnitř static/.
        if not target.is_file() or STATIC_DIR not in target.parents:
            self._send_error_json(404, "Nenalezeno.")
            return
        self._send_file(target)

    # ----------------------------------------------------------------- POST

    def do_POST(self) -> None:  # noqa: N802 — API http.server
        path = self.path.split("?", 1)[0]
        if path == "/api/sync":
            self._handle_sync()
            return
        if path == "/api/alerts":
            payload = self._read_json_body()
            if payload is None:
                return
            self._handle_alerts_post(payload)
            return
        if path == "/api/watchlist":
            self._handle_watchlist_post()
            return
        if path == "/api/portfolio":
            self._handle_portfolio_post()
            return
        if path == "/api/income-target":
            self._handle_income_target_post()
            return
        if path == "/api/scan":
            self._handle_scan_post()
            return
        if path != "/api/analyze":
            self._send_error_json(404, "Nenalezeno.")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send_error_json(400, "Neplatné JSON tělo.")
            return

        tickers_raw = payload.get("tickers") or []
        if not isinstance(tickers_raw, list) or not tickers_raw:
            self._send_error_json(400, "Pole 'tickers' musí být neprázdný seznam.")
            return
        tickers: List[str] = []
        for item in tickers_raw[:MAX_TICKERS_PER_JOB]:
            t = str(item).strip().upper()
            if not TICKER_RE.match(t):
                self._send_error_json(400, f"Neplatný ticker: {item!r}")
                return
            if t not in tickers:
                tickers.append(t)

        try:
            required_return = float(payload.get("required_return_pct", 8.0))
            use_benchmark = bool(payload.get("use_benchmark", True))
        except (TypeError, ValueError):
            self._send_error_json(400, "Neplatné parametry analýzy.")
            return

        job = self.jobs.submit(tickers, required_return, use_benchmark, db=self.db)
        log.info("job %s: analyzuji %s", job.id, ", ".join(tickers))
        self._send_json({"job_id": job.id}, status=202)

    def _handle_sync(self) -> None:
        """Cache-first synchronizace dat do lokální DB (asynchronní job)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send_error_json(400, "Neplatné JSON tělo.")
            return

        tickers_raw = payload.get("tickers") or []
        if not isinstance(tickers_raw, list) or not tickers_raw:
            self._send_error_json(400, "Pole 'tickers' musí být neprázdný seznam.")
            return
        tickers: List[str] = []
        for item in tickers_raw[:MAX_TICKERS_PER_JOB]:
            t = str(item).strip().upper()
            if not TICKER_RE.match(t):
                self._send_error_json(400, f"Neplatný ticker: {item!r}")
                return
            if t not in tickers:
                tickers.append(t)

        force = bool(payload.get("force", False))
        job = self.jobs.submit_sync(self.db, tickers, force)
        log.info("sync job %s: cache-first sync pro %s (force=%s)", job.id, ", ".join(tickers), force)
        self._send_json({"job_id": job.id}, status=202)

    def _handle_watchlist_post(self) -> None:
        """Přidá ticker do trvalé watchlisty (+ JSON záloha)."""
        from ..data import watchlist_store

        payload = self._read_json_body()
        if payload is None:
            return
        ticker = self._valid_ticker(payload.get("ticker"))
        if ticker is None:
            return
        note = str(payload.get("note") or "").strip() or None
        added = not self.db.is_in_watchlist(ticker)
        self.db.upsert_watchlist(ticker, note=note)  # COALESCE — existující poznámka zůstane
        if added:
            try:
                # explicitní cesta (modulová konstanta), aby testy mohly zálohu přesměrovat
                watchlist_store.export_watchlist_file(self.db, watchlist_store.DEFAULT_WATCHLIST_FILE)
            except OSError as exc:
                log.warning("záloha watchlisty se nepodařila: %s", exc)
        self._send_json({"ok": True, "watchlist": {"ticker": ticker, "note": note}}, status=201)

    def _handle_portfolio_post(self) -> None:
        """Přidá/aktualizuje drženou pozici (+ automaticky do trvalé watchlisty)."""
        from ..data.watchlist_store import auto_add_to_watchlist

        payload = self._read_json_body()
        if payload is None:
            return
        ticker = self._valid_ticker(payload.get("ticker"))
        if ticker is None:
            return
        try:
            shares = float(payload["shares"])
        except (KeyError, TypeError, ValueError):
            self._send_error_json(400, "Chybí nebo je neplatné 'shares' (počet akcií).")
            return
        if shares <= 0:
            self._send_error_json(400, "'shares' musí být kladné číslo.")
            return
        avg_cost = payload.get("avg_cost")
        try:
            avg_cost_f = float(avg_cost) if avg_cost is not None else None
        except (TypeError, ValueError):
            self._send_error_json(400, "Neplatná 'avg_cost' (nákupní cena).")
            return
        currency = str(payload.get("currency") or "USD").strip().upper()[:3] or "USD"
        self.db.upsert_position(ticker, shares=shares, avg_cost=avg_cost_f, currency=currency)
        auto_add_to_watchlist(self.db, [ticker])  # pozice patří do trvalé watchlisty (jako CLI)
        log.info("portfolio: pozice %s aktualizována (%.2f ks)", ticker, shares)
        self._send_json({"ok": True}, status=201)

    def _handle_income_target_post(self) -> None:
        """Nastaví (nebo smaže při amount <= 0) cíl ročního příjmu."""
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            amount = float(payload["amount"])
        except (KeyError, TypeError, ValueError):
            self._send_error_json(400, "Chybí nebo je neplatné 'amount' (roční příjem).")
            return
        if amount < 0:
            self._send_error_json(400, "'amount' musí být nezáporné číslo.")
            return
        if amount > 0:
            self.db.set_setting("income_target", str(amount))
        else:
            self.db.delete_setting("income_target")
        from ..data.portfolio import build_income_target_report

        self._send_json({"ok": True, "income_target": build_income_target_report(self.db)})

    def _handle_scan_post(self) -> None:
        """Asynchronní scan (jako CLI ``divtrack scan``): zelený flag + BUY_ZONE.

        Tělo: ``{tickers?: [...], required_return_pct?, use_benchmark?}``. Bez
        ``tickers`` se skenuje celá trvalá watchlista z DB (stejně jako CLI).
        """
        payload = self._read_json_body()
        if payload is None:
            return

        tickers_raw = payload.get("tickers") or []
        tickers: List[str] = []
        if isinstance(tickers_raw, list) and tickers_raw:
            for item in tickers_raw[:MAX_TICKERS_PER_JOB]:
                t = str(item).strip().upper()
                if not TICKER_RE.match(t):
                    self._send_error_json(400, f"Neplatný ticker: {item!r}")
                    return
                if t not in tickers:
                    tickers.append(t)
        else:
            # bez explicitních tickerů → celá trvalá watchlista (jako CLI)
            tickers = [r["ticker"] for r in self.db.list_watchlist()]

        if not tickers:
            self._send_error_json(400, "Žádné tickery ke skenování — naplňte watchlistu nebo předejte 'tickers'.")
            return

        try:
            required_return = float(payload.get("required_return_pct", 8.0))
            use_benchmark = bool(payload.get("use_benchmark", True))
        except (TypeError, ValueError):
            self._send_error_json(400, "Neplatné parametry scanu.")
            return

        job = self.jobs.submit_scan(tickers, required_return, use_benchmark, db=self.db)
        log.info("scan job %s: skenuji %s", job.id, ", ".join(tickers))
        self._send_json({"job_id": job.id}, status=202)

    # ----------------------------------------------------------------- DELETE

    def do_DELETE(self) -> None:  # noqa: N802 — API http.server
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/reports/") and path != "/api/reports":
            ticker = path.rsplit("/", 1)[-1].upper()
            if not TICKER_RE.match(ticker):
                self._send_error_json(400, f"Neplatný ticker: {ticker!r}")
                return
            self.db.delete_report(ticker)
            self._send_json({"ok": True})
            return
        if path.startswith("/api/watchlist/") and path != "/api/watchlist":
            ticker = path.rsplit("/", 1)[-1].upper()
            if not TICKER_RE.match(ticker):
                self._send_error_json(400, f"Neplatný ticker: {ticker!r}")
                return
            from ..data import watchlist_store

            self.db.remove_from_watchlist(ticker)
            try:
                # explicitní cesta (modulová konstanta), aby testy mohly zálohu přesměrovat
                watchlist_store.export_watchlist_file(self.db, watchlist_store.DEFAULT_WATCHLIST_FILE)
            except OSError as exc:
                log.warning("záloha watchlisty se nepodařila: %s", exc)
            self._send_json({"ok": True})
            return
        if path.startswith("/api/portfolio/") and path != "/api/portfolio":
            ticker = path.rsplit("/", 1)[-1].upper()
            if not TICKER_RE.match(ticker):
                self._send_error_json(400, f"Neplatný ticker: {ticker!r}")
                return
            self.db.remove_position(ticker)
            log.info("portfolio: pozice %s smazána", ticker)
            self._send_json({"ok": True})
            return
        if not (path.startswith("/api/alerts/") and path != "/api/alerts"):
            self._send_error_json(404, "Nenalezeno.")
            return
        try:
            alert_id = int(path.rsplit("/", 1)[-1])
        except ValueError:
            self._send_error_json(400, "Neplatné ID upozornění.")
            return
        self.db.remove_alert(alert_id)
        self._send_json({"ok": True})

    # ------------------------------------------------------------------ log

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — API http.server
        log.debug("%s %s", self.address_string(), format % args)


def create_app(
    host: str = "127.0.0.1",
    port: int = 8742,
    job_manager: Optional[JobManager] = None,
    db_path: Path | str | None = None,
) -> ThreadingHTTPServer:
    """Vytvoří HTTP server s injektovaným :class:`JobManager` a lokální DB."""
    manager = job_manager or JobManager()
    database = Database(db_path if db_path is not None else DEFAULT_DB_PATH)

    class _Handler(WebApp):
        jobs = manager  # type: ignore[assignment]
        db = database  # type: ignore[assignment]

    return ThreadingHTTPServer((host, port), _Handler)


def main(host: str = "127.0.0.1", port: int = 8742) -> None:
    """Vstupní bod ``divtrack web`` — spustí server a čeká na Ctrl+C."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    manager = JobManager()
    database = Database(DEFAULT_DB_PATH)

    class _Handler(WebApp):
        jobs = manager  # type: ignore[assignment]
        db = database  # type: ignore[assignment]

    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"Dividend Tracker web GUI běží na http://{host}:{port} (Ctrl+C pro ukončení)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager._executor.shutdown(wait=False)  # noqa: SLF001 — úklid při výstupu
        database.close()
        httpd.server_close()
