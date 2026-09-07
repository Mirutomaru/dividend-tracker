"""Offline testy Tiingo provideru — klient, normalizace dividend, chain a ceny.

Žádná síť: HTTP vrstva se mockuje na úrovni :func:`dividend_tracker.data.tiingo._get_json`
(jako u FMP v ``test_sync_cache_first.py``).
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.sync import (
    DIVIDEND_TTL,
    _dividend_rows_from_tiingo,
    sync_fundamentals,
)
from dividend_tracker.data.tiingo import (
    DAILY_LIMIT,
    TiingoCacheClient,
    TiingoError,
    TiingoLimitExceeded,
    recent_window,
)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "tiingo_test.db"))
    yield d
    d.close()


# ------------------------------------------------------------------ klient

def test_client_not_configured_without_key(db, monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    client = TiingoCacheClient(db)
    assert not client.configured
    assert not client.can_call()


def test_usage_and_limit_tracking(db, monkeypatch):
    import dividend_tracker.data.tiingo as tiingo_mod

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", lambda *a, **k: [])

    client = TiingoCacheClient(db)
    assert client.configured and client.can_call()

    # Každé volání se zapíše do čítače.
    for _ in range(3):
        client.get_dividend_history("KO")
    u = client.usage()
    assert u["calls_used"] == 3
    assert u["daily_limit"] == DAILY_LIMIT
    assert u["remaining"] == DAILY_LIMIT - 3

    # Vyčerpání limitu → další volání zvedne TiingoLimitExceeded.
    for _ in range(DAILY_LIMIT - 3):
        client.get_dividend_history("KO")
    with pytest.raises(TiingoLimitExceeded):
        client.get_dividend_history("KO")


def test_429_raises_limit_exceeded(monkeypatch):
    import urllib.error

    import dividend_tracker.data.tiingo as tiingo_mod

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("dividend_tracker.data.tiingo.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TiingoLimitExceeded):
        tiingo_mod._get_json("https://api.tiingo.com/tiingo/daily/KO", "test-key")


def test_401_raises_error(monkeypatch):
    import urllib.error

    import dividend_tracker.data.tiingo as tiingo_mod

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("dividend_tracker.data.tiingo.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TiingoError):
        tiingo_mod._get_json("https://api.tiingo.com/tiingo/daily/KO", "bad-key")


def test_auth_header_uses_token_scheme(monkeypatch):
    """Tiingo očekává ``Authorization: Token <klíč>`` — Bearer vrací 403 „Please supply a token“."""
    import json as _json

    import dividend_tracker.data.tiingo as tiingo_mod

    captured = {}

    class FakeResp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return FakeResp(_json.dumps({"ticker": "KO"}).encode())

    monkeypatch.setattr(tiingo_mod.urllib.request, "urlopen", fake_urlopen)
    data = tiingo_mod._get_json("https://api.tiingo.com/tiingo/daily/KO", "sekret")
    assert captured["auth"] == "Token sekret"
    assert data == {"ticker": "KO"}


def test_recent_window_covers_today():
    start, end = recent_window(days=10)
    assert end == date.today().isoformat()
    assert (date.fromisoformat(end) - date.fromisoformat(start)).days == 10


# ------------------------------------------------------------------ normalizace

def _tiingo_dividend(ex_date: str, amount: float, freq: str = "q", payment=None):
    return {
        "exDate": ex_date,
        "paymentDate": payment or (date.fromisoformat(ex_date) + timedelta(days=15)).isoformat(),
        "recordDate": None,
        "distribution": amount,
        "distributionFreqency": freq,  # ano, v API je překlep „Freqency“
    }


def test_normalize_splits_history_and_calendar():
    today = date.today()
    past = (today - timedelta(days=30)).isoformat()
    future = (today + timedelta(days=20)).isoformat()

    hist_rows, cal_rows = _dividend_rows_from_tiingo(
        "KO", [_tiingo_dividend(past, 1.94), _tiingo_dividend(future, 1.98)]
    )
    assert len(hist_rows) == 1 and len(cal_rows) == 1
    assert hist_rows[0]["ex_date"] == past
    assert hist_rows[0]["dividend_amount"] == pytest.approx(1.94)
    assert hist_rows[0]["frequency"] == "quarterly"
    assert cal_rows[0]["ex_date"] == future


def test_normalize_frequency_map():
    today = date.today() - timedelta(days=60)
    cases = {
        "m": "monthly",
        "q": "quarterly",
        "a": "annual",
        "sa": "semiannual",
        "ir": "irregular",
        "u": None,
    }
    for code, expected in cases.items():
        hist_rows, _ = _dividend_rows_from_tiingo("KO", [_tiingo_dividend(today.isoformat(), 1.0, freq=code)])
        assert hist_rows[0]["frequency"] == expected, code


def test_normalize_skips_invalid_items():
    today = date.today() - timedelta(days=30)
    rows_in = [
        _tiingo_dividend(today.isoformat(), 1.0),
        {"exDate": None, "distribution": 1.0},          # bez ex-date
        {"exDate": today.isoformat(), "distribution": None},  # bez částky
    ]
    hist_rows, cal_rows = _dividend_rows_from_tiingo("KO", rows_in)
    assert len(hist_rows) == 1 and not cal_rows


def test_dividend_history_extracted_from_bars(db, monkeypatch):
    """get_dividend_history čte ex-date z pole divCash v cenových barech."""
    import dividend_tracker.data.tiingo as tiingo_mod

    today = date.today()
    bars = [
        {"date": (today - timedelta(days=90)).isoformat() + "T00:00:00+00:00", "close": 60.0, "divCash": 1.94},
        {"date": (today - timedelta(days=30)).isoformat() + "T00:00:00+00:00", "close": 61.0, "divCash": 2.05},
        {"date": today.isoformat() + "T00:00:00+00:00", "close": 62.0, "divCash": 0.0},
    ]

    def fake(url, key, timeout=15.0):
        assert "/daily/KO/prices" in url and "startDate=" in url
        return bars

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    client = TiingoCacheClient(db)
    items = client.get_dividend_history("KO")
    assert [i["exDate"] for i in items] == [
        (today - timedelta(days=90)).isoformat(),
        (today - timedelta(days=30)).isoformat(),
    ]
    assert [i["distribution"] for i in items] == pytest.approx([1.94, 2.05])
    # divCash nezná payment/record date ani frekvenci — normalizace je zvládne.
    hist_rows, cal_rows = _dividend_rows_from_tiingo("KO", items)
    assert len(hist_rows) == 2 and not cal_rows


# ------------------------------------------------------------------ sync_via_tiingo

def test_sync_via_tiingo_writes_db(db, monkeypatch):
    import dividend_tracker.data.tiingo as tiingo_mod

    today = date.today()
    past = (today - timedelta(days=30)).isoformat()

    def fake(url, key, timeout=15.0):
        if "/prices" in url:  # bary — dividendy se odvozují z pole divCash
            return [
                {"date": f"{past}T00:00:00+00:00", "close": 60.0, "divCash": 1.94},
                {"date": today.isoformat() + "T00:00:00+00:00", "close": 61.0, "divCash": 0.0},
            ]
        return {"name": "Coca-Cola Co.", "exchangeCode": "NYSE"}  # meta (bez měny)

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    from dividend_tracker.data.sync import sync_via_tiingo

    client = TiingoCacheClient(db)
    sync_via_tiingo(client, db, "ko")  # malá písmena → normalizace na KO

    stock = db.get_stock("KO")
    assert stock is not None and stock["name"] == "Coca-Cola Co."
    assert stock["exchange"] == "NYSE"  # exchangeCode → exchange
    divs = db.get_dividends("KO")
    assert len(divs) == 1 and divs[0]["ex_date"] == past
    cal = [r for r in db.get_calendar("KO") if r["ticker"] == "KO"]
    assert not cal  # z historických barech se budoucí výplata odvodit nedá
    # Čítač: ceny s divCash (1) + meta při prvním syncu (1).
    assert client.usage()["calls_used"] == 2


def test_sync_via_tiingo_no_meta_call_when_stock_exists(db, monkeypatch):
    import dividend_tracker.data.tiingo as tiingo_mod

    calls = []

    def fake(url, key, timeout=15.0):
        calls.append(url)
        return [
            {
                "date": (date.today() - timedelta(days=30)).isoformat() + "T00:00:00+00:00",
                "close": 60.0,
                "divCash": 1.94,
            }
        ]

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    from dividend_tracker.data.sync import sync_via_tiingo

    db.upsert_stock("KO", name="Coca-Cola Co.")  # profil už existuje → meta se netaží
    client = TiingoCacheClient(db)
    sync_via_tiingo(client, db, "KO")
    assert len(calls) == 1 and "/daily/KO/prices" in calls[0]


# ------------------------------------------------------------------ provider chain

def _tiingo_bars_with_dividend(ex_date: str, amount: float):
    """Denní bary, z nichž se dividendová historie odvozuje přes divCash."""
    return [
        {"date": f"{ex_date}T00:00:00+00:00", "close": 60.0, "divCash": amount},
        {"date": date.today().isoformat() + "T00:00:00+00:00", "close": 61.0, "divCash": 0.0},
    ]


def test_chain_uses_tiingo_when_fmp_missing(db, monkeypatch):
    import dividend_tracker.data.tiingo as tiingo_mod

    def fake(url, key, timeout=15.0):
        return _tiingo_bars_with_dividend((date.today() - timedelta(days=30)).isoformat(), 1.94)

    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    db.upsert_stock("KO", name="Coca-Cola Co.")
    with db._conn() as conn:  # noqa: SLF001 — vynutit stale stav
        conn.execute(
            "UPDATE stocks SET last_fundamental_update=?, last_dividend_sync=? WHERE ticker='KO'",
            (time.time() - DIVIDEND_TTL - 1, time.time() - DIVIDEND_TTL - 1),
        )

    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "tiingo"
    assert len(db.get_dividends("KO")) == 1


def test_chain_fmp_error_falls_to_tiingo(db, monkeypatch):
    import dividend_tracker.data.fmp as fmp_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    def fake(url, key, timeout=15.0):
        return _tiingo_bars_with_dividend((date.today() - timedelta(days=30)).isoformat(), 1.94)

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(
        fmp_mod, "_get_json", lambda *a, **k: (_ for _ in ()).throw(fmp_mod.FMPError("fmp down"))
    )
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    db.upsert_stock("KO", name="Coca-Cola Co.")
    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "tiingo"


def test_chain_tiingo_limit_falls_to_yfinance(db, monkeypatch):
    import dividend_tracker.data.sync as sync_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    for _ in range(DAILY_LIMIT):
        db.record_api_call("tiingo", daily_limit=DAILY_LIMIT)

    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(
        tiingo_mod, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nesmí být voláno"))
    )

    called = []
    monkeypatch.setattr(
        sync_mod, "sync_via_yfinance", lambda db_, tk: called.append(tk) or db_.mark_dividend_sync(tk)
    )

    db.upsert_stock("KO", name="Coca-Cola Co.")
    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "yfinance"
    assert called == ["KO"]


def test_summary_includes_tiingo_usage(db):
    result = sync_fundamentals(db, [])  # bez klíčů — jen struktura souhrnu
    assert "tiingo_usage" in result and "fmp_usage" in result


# ------------------------------------------------------------------ ceny přes Tiingo

def test_fetch_quotes_uses_tiingo_bars(db, monkeypatch):
    import dividend_tracker.data.quotes as quotes_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    today = date.today()
    bars = [
        {"date": (today - timedelta(days=1)).isoformat(), "close": 60.0, "volume": 1_000_000},
        {"date": today.isoformat(), "close": 61.5, "volume": 900_000},
    ]

    def fake(url, key, timeout=15.0):
        assert "/daily/KO" in url and "startDate=" in url
        return bars

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)
    # yfinance by se nemělo volat — Tiingo dodalo vše.
    monkeypatch.setattr(quotes_mod, "_fetch_quotes_yfinance", lambda t: (_ for _ in ()).throw(AssertionError("yfinance by nemělo být voláno")))

    out = quotes_mod.fetch_quotes(["KO"], db=db)
    assert out["KO"]["price"] == pytest.approx(61.5)
    assert out["KO"]["change"] == pytest.approx(1.5)
    assert out["KO"]["volume"] == 900_000


def test_fetch_quotes_falls_back_to_yfinance_for_missing(db, monkeypatch):
    import dividend_tracker.data.quotes as quotes_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    def fake(url, key, timeout=15.0):
        raise TiingoError("unknown symbol")  # Tiingo nezná ticker → fallback

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)
    monkeypatch.setattr(
        quotes_mod, "_fetch_quotes_yfinance",
        lambda t: {"KO": {"price": 60.0, "change": None, "volume": None}},
    )

    out = quotes_mod.fetch_quotes(["KO"], db=db)
    assert out["KO"]["price"] == pytest.approx(60.0)


def test_fetch_price_history_uses_adj_close(db, monkeypatch):
    import dividend_tracker.data.quotes as quotes_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    today = date.today()
    bars = [
        {"date": (today - timedelta(days=2)).isoformat(), "close": 60.0, "adjClose": 59.8},
        {"date": (today - timedelta(days=1)).isoformat(), "close": 61.0, "adjClose": 60.7},
    ]

    def fake(url, key, timeout=15.0):
        return bars

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)
    monkeypatch.setattr(quotes_mod, "_fetch_price_history_yfinance", lambda t, days: (_ for _ in ()).throw(AssertionError("yfinance by nemělo být voláno")))

    out = quotes_mod.fetch_price_history(["KO"], days=365, db=db)
    assert [p[1] for p in out["KO"]] == pytest.approx([59.8, 60.7])  # adjClose, ne close


def test_backfill_uses_tiingo(db, monkeypatch):
    import dividend_tracker.data.quotes as quotes_mod
    import dividend_tracker.data.tiingo as tiingo_mod

    today = date.today()
    bars = [
        {"date": (today - timedelta(days=i)).isoformat(), "close": 60.0 + i, "adjClose": 59.0 + i}
        for i in range(3)
    ]

    def fake(url, key, timeout=15.0):
        return bars

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(tiingo_mod, "_get_json", fake)

    result = quotes_mod.backfill_price_history(db, ["KO"], days=365)
    assert result == {"points": 3, "tickers_with_data": 1}
    hist = db.get_price_history("KO")
    assert len(hist) == 3
