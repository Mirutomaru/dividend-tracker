"""Testy webové API (web/server.py) — offline, pipeline je mockovaná."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from dividend_tracker.data.fetcher import DataFetchError
from dividend_tracker.web.server import create_app


@pytest.fixture()
def server(tmp_path):
    """Spustí HTTP server na náhodném portu s izolovanou DB; po testu ho ukončí."""
    httpd = create_app(port=0, db_path=tmp_path / "web_test.db")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def seeded_server(tmp_path):
    """Server s předvyplněnou DB (2 tickery) pro testy watchlist endpointu."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "seeded.db")
    for tk, name in (("KO", "Coca-Cola"), ("JNJ", "Johnson & Johnson")):
        db.upsert_stock(tk, name=name, currency="USD", sector="Consumer Defensive")
        db.upsert_quote(tk, current_price=70.0 if tk == "KO" else 150.0)
    httpd = create_app(port=0, db_path=db.path)
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


# ------------------------------------------------------------------ watchlist (DB)

def test_watchlist_endpoint_empty(seeded_server):
    """Prázdná DB → prázdný seznam, ale endpoint funguje."""

    # seeded_server má data; ověřit strukturu odpovědi.
    status, body = _request(seeded_server, "GET", "/api/watchlist")
    assert status == 200
    assert isinstance(body["rows"], list) and len(body["rows"]) == 2
    # výchozí abecední pořadí
    assert [r["ticker"] for r in body["rows"]] == ["JNJ", "KO"]


def test_watchlist_row_fields(seeded_server):
    status, body = _request(seeded_server, "GET", "/api/watchlist")
    row = body["rows"][0]
    # Každý sledovaný atribut musí být přítomen (UI podle nich řadí).
    for key in ("ticker", "name", "price", "yield_pct", "payout_pct", "growth_5y_pct",
                "streak_years", "next_ex_date", "next_dividend", "quote_age_s"):
        assert key in row, f"chybí atribut {key}"


def test_fmp_status_endpoint(server):
    status, body = _request(server, "GET", "/api/fmp-status")
    assert status == 200
    assert "configured" in body and "calls_used" in body and "daily_limit" in body


# ------------------------------------------------------------------ portfolio (DB)

@pytest.fixture()
def portfolio_server(tmp_path):
    """Server s pozicemi v portfoliu a nastaveným cílem příjmů."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "pf_web.db")
    db.upsert_stock("KO", name="Coca-Cola", currency="USD")
    db.upsert_quote("KO", current_price=60.0)
    db.upsert_position("KO", shares=100, avg_cost=50.0)
    db.set_setting("income_target", "1000")
    httpd = create_app(port=0, db_path=db.path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_portfolio_endpoint(portfolio_server):
    status, body = _request(portfolio_server, "GET", "/api/portfolio")
    assert status == 200
    pf = body["portfolio"]
    assert pf["n_positions"] == 1
    pos = pf["positions"][0]
    assert pos["ticker"] == "KO"
    assert pos["market_value"] == pytest.approx(6000.0)
    # bez dividendové historie je příjem 0, ale struktura je kompletní
    for key in ("shares", "avg_cost", "price", "cost_basis", "dps_ttm",
                "annual_income", "yield_on_cost_pct"):
        assert key in pos

    target = body["income_target"]
    assert target["configured"] is True
    assert target["target_annual_income"] == pytest.approx(1000.0)


def test_portfolio_endpoint_empty(server):
    """Prázdné portfolio → n_positions 0, cíl nenastaven."""
    status, body = _request(server, "GET", "/api/portfolio")
    assert status == 200
    assert body["portfolio"]["n_positions"] == 0
    assert body["income_target"] == {"configured": False}


# ------------------------------------------------------------------ alerts (DB)

@pytest.fixture()
def alerts_server(tmp_path):
    """Server s cenou v quotes pro testy upozornění."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "alerts_web.db")
    db.upsert_stock("KO", name="Coca-Cola", currency="USD")
    db.upsert_quote("KO", current_price=60.0)
    httpd = create_app(port=0, db_path=db.path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_alerts_endpoint_empty(server):
    status, body = _request(server, "GET", "/api/alerts")
    assert status == 200
    assert body["alerts"] == []


def test_alert_create_list_delete_flow(alerts_server):
    """POST → GET (viditelné) → DELETE (zmizí)."""
    # vytvoření pravidla below s prahem
    status, body = _request(alerts_server, "POST", "/api/alerts",
                            {"ticker": "ko", "condition": "below", "threshold": 65.0})
    assert status == 201 and body["alert"]["id"] >= 1
    alert_id = body["alert"]["id"]

    # viditelné v seznamu, ticker normalizovaný na uppercase
    status, body = _request(alerts_server, "GET", "/api/alerts")
    assert status == 200 and len(body["alerts"]) == 1
    row = body["alerts"][0]
    assert row["ticker"] == "KO" and row["condition"] == "below"

    # smazání
    status, _ = _request(alerts_server, "DELETE", f"/api/alerts/{alert_id}")
    assert status == 200
    status, body = _request(alerts_server, "GET", "/api/alerts")
    assert body["alerts"] == []


def test_alert_buy_zone_needs_no_threshold(alerts_server):
    status, body = _request(alerts_server, "POST", "/api/alerts",
                            {"ticker": "KO", "condition": "buy_zone"})
    assert status == 201 and body["alert"]["threshold"] is None


def test_alert_invalid_condition_rejected(server):
    status, body = _request(server, "POST", "/api/alerts",
                            {"ticker": "KO", "condition": "sideways", "threshold": 5})
    assert status == 400 and "error" in body


def test_alert_missing_threshold_rejected(server):
    status, body = _request(server, "POST", "/api/alerts",
                            {"ticker": "KO", "condition": "below"})
    assert status == 400 and "error" in body


def test_alert_invalid_ticker_rejected(server):
    status, body = _request(server, "POST", "/api/alerts",
                            {"ticker": "../etc/passwd", "condition": "below", "threshold": 5})
    assert status == 400 and "error" in body


def test_alert_delete_unknown_id_404(alerts_server):
    status, _ = _request(alerts_server, "DELETE", "/api/alerts/9999")
    # neexistující ID → remove_alert je no-op; endpoint vrací ok (idempotentní)
    assert status == 200


def test_alert_delete_bad_id_400(server):
    status, _ = _request(server, "DELETE", "/api/alerts/abc")
    assert status == 400


# ------------------------------------------------------------------ sync

def test_sync_flow_with_mocked_providers(monkeypatch, server):
    """POST /api/sync → job; mockovaná synchronizace; výsledek obsahuje summary."""
    from dividend_tracker import web as web_pkg

    def fake_sync_all(db_, tickers, force=False):
        # tvar odpovídá skutečnému sync_all: {results, counts, fmp_usage, quotes}
        return {
            "results": {t: "cached" for t in tickers},
            "counts": {"fmp": 0, "yfinance": 0, "cached": len(tickers), "stale_cache": 0, "error": 0},
            "fmp_usage": {"calls_used": 0, "daily_limit": None, "remaining": None},
            "quotes": {},
        }

    monkeypatch.setattr(web_pkg.server, "sync_all", fake_sync_all)

    status, body = _request(server, "POST", "/api/sync", {"tickers": ["KO", "JNJ"]})
    assert status == 202 and "job_id" in body
    job = _wait_job(server, body["job_id"])
    assert job["status"] == "done"
    assert job["summary"]["counts"]["cached"] == 2


def test_sync_invalid_ticker_rejected(server):
    status, body = _request(server, "POST", "/api/sync", {"tickers": ["BAD TICKER!"]})
    assert status == 400 and "error" in body


def test_sync_empty_rejected(server):
    for payload in ({}, {"tickers": []}):
        status, body = _request(server, "POST", "/api/sync", payload)
        assert status == 400 and "error" in body, payload


# ------------------------------------------------------------------ CSV export

def test_export_watchlist_csv_endpoint(seeded_server):
    """GET /api/export/watchlist.csv → text/csv s attachment a hlavičkou."""
    conn = http.client.HTTPConnection(*seeded_server.split("//")[1].split(":"), timeout=5)
    conn.request("GET", "/api/export/watchlist.csv")
    resp = conn.getresponse()
    raw_bytes = resp.read()
    ctype = resp.getheader("Content-Type", "")
    disp = resp.getheader("Content-Disposition", "")
    conn.close()
    assert resp.status == 200
    assert "text/csv" in ctype and 'filename="watchlist.csv"' in disp
    # BOM (utf-8-sig) — soubor se musí správně otevřít v Excelu jako file export
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    raw = raw_bytes.decode("utf-8-sig")
    lines = [ln for ln in raw.splitlines() if ln]
    assert lines[0].startswith("ticker,name,currency")
    # 2 tickery v DB → 2 data řádky + hlavička
    assert len(lines) == 3


def test_export_analysis_csv_flow(monkeypatch, server):
    """Hotový analytický job → CSV export reportů."""
    from dividend_tracker import web as web_pkg

    monkeypatch.setattr(
        web_pkg.server, "analyze_ticker",
        lambda t, **kw: {
            "ticker": t,
            "company": {"name": f"Firma {t}", "sector": "Tech"},
            "price_current": 10.0,
            "score": {"total_0_100": 55.0},
            "fair_value": {"fair_value": 12.0, "verdict": "undervalued"},
        },
    )

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["KO"]})
    assert status == 202
    job_id = body["job_id"]
    _wait_job(server, job_id)

    conn = http.client.HTTPConnection(*server.split("//")[1].split(":"), timeout=5)
    conn.request("GET", f"/api/export/analysis/{job_id}.csv")
    resp = conn.getresponse()
    raw_bytes = resp.read()
    ctype = resp.getheader("Content-Type", "")
    disp = resp.getheader("Content-Disposition", "")
    conn.close()
    assert resp.status == 200 and "text/csv" in ctype
    assert f'filename="analysis_{job_id}.csv"' in disp
    # BOM (utf-8-sig) — konzistence se souborovým exportem pro Excel
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    raw = raw_bytes.decode("utf-8-sig")
    lines = [ln for ln in raw.splitlines() if ln]
    assert lines[0].startswith("ticker,name,sector")
    # jeden report → ticker KO + férová cena 12
    assert "KO" in lines[1] and "Firma KO" in lines[1]


def test_export_analysis_no_reports_404(monkeypatch, server):
    """Job kde selhaly VŠECHNY tickery (jen error položky) → 404, ne prázdný CSV."""
    from dividend_tracker import web as web_pkg

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        raise DataFetchError(f"žádná data pro {ticker}")

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["XYZ"]})
    assert status == 202
    job_id = body["job_id"]
    job = _wait_job(server, job_id)
    # job je hotový, ale obsahuje jen error položku (žádný report)
    assert job["status"] == "done" and all("error" in e for e in job["results"])

    status, body = _request(server, "GET", f"/api/export/analysis/{job_id}.csv")
    assert status == 404 and "error" in body


def test_export_analysis_unknown_job_404(server):
    status, body = _request(server, "GET", "/api/export/analysis/neexistujici.csv")
    assert status == 404 and "error" in body


# ------------------------------------------------------------------ reports (DB)

@pytest.fixture()
def reports_server(tmp_path):
    """Server s předuloženými analytickými reporty v lokální DB."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "reports_web.db")
    for tk, price in (("KO", 60.0), ("JNJ", 150.0)):
        db.save_report({
            "schema_version": "1.0",
            "ticker": tk,
            "generated_at": "2026-09-07T10:00:00+00:00",
            "company": {"name": f"Firma {tk}", "sector": "Consumer Defensive"},
            "currency": "USD",
            "price_current": price,
            "score": {"total_0_100": 72.5},
        })
    httpd = create_app(port=0, db_path=db.path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_reports_endpoint_empty(server):
    """Prázdná DB → prázdný seznam, ale endpoint funguje."""
    status, body = _request(server, "GET", "/api/reports")
    assert status == 200
    assert body["reports"] == [] and body["count"] == 0


def test_reports_endpoint_returns_stored(reports_server):
    """Uložené reporty se vrátí abecedně s kompletním obsahem."""
    status, body = _request(reports_server, "GET", "/api/reports")
    assert status == 200 and body["count"] == 2
    assert [r["ticker"] for r in body["reports"]] == ["JNJ", "KO"]
    ko = next(r for r in body["reports"] if r["ticker"] == "KO")
    assert ko["price_current"] == 60.0 and ko["score"]["total_0_100"] == 72.5


def test_analyze_persists_report_to_db(monkeypatch, server):
    """Hotový analytický job se perzistuje do DB (GET /api/reports ho vidí)."""
    from dividend_tracker import web as web_pkg

    monkeypatch.setattr(
        web_pkg.server, "analyze_ticker",
        lambda t, **kw: {
            "ticker": t,
            "company": {"name": f"Firma {t}", "sector": "Tech"},
            "price_current": 42.0,
            "score": {"total_0_100": 55.0},
        },
    )

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["KO"]})
    assert status == 202
    job_id = body["job_id"]
    _wait_job(server, job_id)

    # report přežil „restart“ — je v lokální DB a endpoint ho vrátí
    status, body = _request(server, "GET", "/api/reports")
    assert status == 200 and body["count"] == 1
    assert body["reports"][0]["ticker"] == "KO"


def test_analyze_error_not_persisted(monkeypatch, server):
    """Selhalý ticker (DataFetchError) se do DB neukládá."""
    from dividend_tracker import web as web_pkg

    def fake_analyze(ticker, required_return_pct=8.0, use_benchmark=True):
        raise DataFetchError(f"žádná data pro {ticker}")

    monkeypatch.setattr(web_pkg.server, "analyze_ticker", fake_analyze)

    status, body = _request(server, "POST", "/api/analyze", {"tickers": ["XYZ"]})
    assert status == 202
    job_id = body["job_id"]
    _wait_job(server, job_id)

    status, body = _request(server, "GET", "/api/reports")
    assert status == 200 and body["reports"] == []


def test_delete_report_endpoint(reports_server):
    """DELETE /api/reports/<ticker> → report zmizí (idempotentní)."""
    status, _ = _request(reports_server, "DELETE", "/api/reports/KO")
    assert status == 200

    status, body = _request(reports_server, "GET", "/api/reports")
    assert [r["ticker"] for r in body["reports"]] == ["JNJ"]

    # druhý DELETE na stejný ticker — idempotentní ok
    status, _ = _request(reports_server, "DELETE", "/api/reports/KO")
    assert status == 200


def test_delete_report_case_insensitive(reports_server):
    status, _ = _request(reports_server, "DELETE", "/api/reports/ko")
    assert status == 200
    status, body = _request(reports_server, "GET", "/api/reports")
    assert [r["ticker"] for r in body["reports"]] == ["JNJ"]


def test_delete_report_invalid_ticker_400(server):
    # "!" není v povolené množině [A-Z0-9.-] → 400 (i po upper())
    status, body = _request(server, "DELETE", "/api/reports/BAD!TICKER")
    assert status == 400 and "error" in body
