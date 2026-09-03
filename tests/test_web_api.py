"""Testy webové API (web/server.py) — offline, pipeline je mockovaná."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from dividend_tracker.data.fetcher import DataFetchError
from dividend_tracker.web.server import JobManager, create_app


@pytest.fixture()
def server():
    """Spustí HTTP server na náhodném portu a po testu ho ukončí."""
    httpd = create_app(port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def _request(server: str, method: str, path: str, body: dict | None = None):
    """Vrátí (status_code, parsed_json_or_text)."""
    conn = http.client.HTTPConnection(*server.split("//")[1].split(":"), timeout=5)
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, raw


def _wait_job(server: str, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, job = _request(server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if job["status"] == "done":
            return job
        time.sleep(0.05)
    raise AssertionError("job nedokončen v časovém limitu")


# ------------------------------------------------------------------ statické

def test_health(server):
    status, body = _request(server, "GET", "/api/health")
    assert status == 200 and body == {"status": "ok"}


def test_index_served(server):
    status, body = _request(server, "GET", "/")
    assert status == 200
    assert "Dividend Tracker" in body


def test_static_assets_served(server):
    for path in ("/static/style.css", "/static/app.js"):
        status, _ = _request(server, "GET", path)
        assert status == 200, path


def test_path_traversal_blocked(server):
    status, _ = _request(server, "GET", "/static/../server.py")
    assert status == 404


# ------------------------------------------------------------------ analýza

def test_analyze_flow_with_mocked_pipeline(monkeypatch, server):
    """Kompletní tok: POST → job_id → polling → report (mockovaná pipeline)."""
    from dividend_tracker import web as web_pkg

    calls = []

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        calls.append((ticker, required_return_pct, use_benchmark))
        return {"schema_version": "1.0", "ticker": ticker, "price_current": 42.0}

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["ko", "JNJ"]})
    assert status == 202 and "job_id" in body
    job = _wait_job(server, body["job_id"])
    assert job["status"] == "done" and job["total"] == 2 and len(job["results"]) == 2
    # tickery normalizované na uppercase
    assert {r["ticker"] for r in job["results"]} == {"KO", "JNJ"}
    assert all("report" in r for r in job["results"])
    assert calls[0][1] == 8.0 and calls[0][2] is True


def test_analyze_error_entry(monkeypatch, server):
    """DataFetchError se zachytí do error položky, job dokončí."""
    from dividend_tracker import web as web_pkg

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        raise DataFetchError(f"žádná data pro {ticker}")

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["XYZ"]})
    assert status == 202
    job = _wait_job(server, body["job_id"])
    assert len(job["results"]) == 1
    assert "error" in job["results"][0] and "report" not in job["results"][0]


def test_analyze_unexpected_error(monkeypatch, server):
    """Neočekávaná výjimka nesmí zabít job."""
    from dividend_tracker import web as web_pkg

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["ABC"]})
    assert status == 202
    job = _wait_job(server, body["job_id"])
    assert "error" in job["results"][0]


# ------------------------------------------------------------------ validace

def test_invalid_ticker_rejected(server):
    for bad in (["../etc/passwd"], ["A B C"], ["x" * 20], ["1; rm -rf /"]):
        status, body = _request(server, "POST", "/api/analyze", {"tickers": bad})
        assert status == 400 and "error" in body, bad


def test_empty_tickers_rejected(server):
    for payload in ({}, {"tickers": []}, {"tickers": ["   "]}, {"tickers": "KO"}):
        status, body = _request(server, "POST", "/api/analyze", payload)
        assert status == 400 and "error" in body, payload


def test_malformed_json_rejected(server):
    conn = http.client.HTTPConnection(*server.split("//")[1].split(":"), timeout=5)
    conn.request("POST", "/api/analyze", body=b"{invalid json", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_unknown_job_404(server):
    status, _ = _request(server, "GET", "/api/jobs/neexistujici")
    assert status == 404


def test_dedup_and_limit(monkeypatch, server):
    """Duplicitní tickery se deduplikují; limit 100 tickerů na job."""
    from dividend_tracker import web as web_pkg

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", lambda t, **kw: {"ticker": t})

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["KO", "ko", "Ko"]})
    assert status == 202
    job = _wait_job(server, body["job_id"])
    assert job["total"] == 1

    many = [f"T{i:03d}" for i in range(105)]
    status, body = _request(server, "POST", "/api/analyze", {"tickers": many})
    assert status == 202
    job = _wait_job(server, body["job_id"], timeout=15.0)
    assert job["total"] == 100


def test_custom_params_forwarded(monkeypatch, server):
    from dividend_tracker import web as web_pkg

    seen = {}

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        seen.update(required_return_pct=required_return_pct, use_benchmark=use_benchmark)
        return {"ticker": ticker}

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)
    status, body = _request(
        server, "POST", "/api/analyze",
        {"tickers": ["KO"], "required_return_pct": 10.5, "use_benchmark": False},
    )
    assert status == 202
    _wait_job(server, body["job_id"])
    assert seen == {"required_return_pct": 10.5, "use_benchmark": False}


def test_invalid_params_rejected(server):
    status, _ = _request(server, "POST", "/api/analyze", {"tickers": ["KO"], "required_return_pct": "osm"})
    assert status == 400
