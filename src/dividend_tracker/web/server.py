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
  GET  /api/fmp-status     stav čítače FMP denních volání (limit 250/den)
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

from ..data.db import Database
from ..data.fetcher import DataFetchError
from ..data.sync import sync_all
from ..data.watchlist import build_watchlist_rows
from ..pipeline import analyze_ticker

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Výchozí cesta lokální databáze (přesahuje restarty — cache-first úložiště).
DEFAULT_DB_PATH = Path(os.environ.get("DIVTRACK_DB", "~/.cache/dividend-tracker/data.db")).expanduser()

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

    def submit(self, tickers: List[str], required_return_pct: float, use_benchmark: bool) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], total=len(tickers))
        with self._lock:
            self._jobs[job.id] = job
        for ticker in tickers:
            self._executor.submit(self._run_one, job, ticker, required_return_pct, use_benchmark)
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

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run_one(
        self, job: Job, ticker: str, required_return_pct: float, use_benchmark: bool
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


class WebApp(BaseHTTPRequestHandler):
    """HTTP handler — statické soubory + JSON API."""

    server_version = "DividendTracker/0.1"
    # JobManager a Database se injectují v :func:`create_app` (třídová atributová reference).
    jobs: JobManager  # type: ignore[valid-type]
    db: Database  # type: ignore[valid-type]

    # ------------------------------------------------------------------ util

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

        job = self.jobs.submit(tickers, required_return, use_benchmark)
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
