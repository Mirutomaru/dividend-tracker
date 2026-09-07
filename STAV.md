# STAV.md — orientace pro agenta (a uživatele)

Tento soubor je **jediný zdroj pravdy o stavu projektu**. Před začátkem práce ho
přečti, po dokončení každého úkolu dopiš do „Log postupů“ a uprav „Co je hotové“.
Cíl: kdykoli se agent (nebo uživatel) vrátí, za 2 minuty ví, kde jsme.

> Projekt: **dividend-tracker** — `/home/ai/Prace/projekty/dividend-tracker`
> Python ≥3.10 (venv `.venv`, Python 3.13), CLI `divtrack`, web GUI `divtrack web` / `./start.sh`.

---

## 1) Rychlá mapa projektu (Clean Architecture)

```
src/dividend_tracker/
├── models.py            Pydantic modely (Snapshot → MetricsReport → Score/FairValue)
├── domain/              čistá logika, bez sítě/UI — 100 % pokryto offline testy
│   ├── dividends.py     agregace plateb, CAGR, streak, split-adjustment
│   ├── metrics.py       metriky A (dividendy), B (valuace), C (solvence)
│   ├── scoring.py       skóre 0–100 + safety flag + signály (BUY_ZONE / DIVIDEND_TRAP)
│   ├── valuation.py     Gordon, two-stage DDM, výnosová metoda, nákupní zóna
│   ├── tax.py           srážková daň podle země původu (sazby přepisovatelné)
│   └── sectors.py       odvětví + REIT detekce (is_reit_like)
├── data/                sběr dat + SQLite cache-first DB (~/.cache/dividend-tracker/data.db)
│   ├── fetcher.py       yfinance snapshoty, retry+backoff, disk cache TTL 1d, benchmarky sektorů
│   ├── fmp.py           FMP klient (fundamenty), denní limit 250 volání (tabulka api_usage)
│   ├── quotes.py        ceny přes yfinance → quotes + denní price_history snapshoty
│   ├── sync.py          cache-first sync (fundamenty TTL 72 h, dividendy 7 dní) + alerty
│   ├── watchlist.py     build_watchlist_rows() — srovnávací řádky PŘÍMO Z DB (offline)
│   ├── watchlist_store.py  trvalá watchlista: SQLite + JSON záloha (~/.config/...)
│   ├── portfolio.py     report portfolia + kalkulačka cíle ročního příjmu (rentier plán)
│   ├── alerts.py        pravidla below/above/buy_zone, vyhodnocení proti cenám v DB
│   └── db.py            Database — všechny tabulky a CRUD metody
├── pipeline.py          analyze_ticker() — jediný vstupní bod (CLI i GUI)
├── presentation/        report.py (JSON schéma), console.py, csv_export.py
├── gui/app.py           Tkinter desktop GUI
├── web/                 Web GUI — stdlib HTTP server, bez závislostí
│   ├── server.py        JobManager + JSON API (viz níže)
│   └── static/          index.html · style.css (Apple-style) · app.js (vanilla JS)
└── cli.py               divtrack analyze/sync/watch/portfolio/alert/export/scan/config/web/gui
```

### Web API (web/server.py, port 8742 výchozí)

| Metoda | Cesta | Co dělá |
|---|---|---|
| GET | `/api/health` | liveness |
| POST | `/api/analyze {tickers}` | asynchronní analýza → job_id; hotové reporty se perzistují do DB (reports) |
| GET | `/api/jobs/<id>` | stav jobu + výsledky |
| GET | `/api/watchlist` | srovnávací řádky z DB (offline), každý řádek má `in_watchlist` flag (★) |
| POST | `/api/watchlist {ticker}` | přidá ticker do trvalé watchlisty (+ JSON záloha) |
| DELETE | `/api/watchlist/<T>` | odebere ★ z trvalé watchlisty |
| POST | `/api/sync {tickers, force}` | cache-first sync jako job; na konci vyhodnotí alerty |
| POST | `/api/scan {tickers?}` | scan jako job (zelený flag + BUY_ZONE); bez tickers = celá trvalá watchlista; `job.extra.matches` po dokončení |
| GET/POST/DELETE | `/api/alerts[/<id>]` | pravidla upozornění |
| GET | `/api/portfolio` | portfolio + cíl ročního příjmu (čtení) |
| POST | `/api/portfolio {ticker, shares, avg_cost?}` | přidá/aktualizuje pozici (+ auto do watchlisty) |
| DELETE | `/api/portfolio/<T>` | smaže pozici z portfolia |
| POST | `/api/income-target {amount}` | nastaví cíl ročního příjmu (0 = odebrat) |
| GET/DELETE | `/api/reports[/<ticker>]` | uložené analytické reporty (přežívají restart) |
| GET | `/api/fmp-status` | zbývající FMP volání dnes |
| GET | `/api/export/watchlist.csv`, `/api/export/analysis/<job>.csv` | CSV exporty (BOM pro Excel) |

---

## 2) Co je hotové (ověřeno testy + commity)

- **Doménová logika** — metriky A/B/C, skóre 0–100 s přepočtem vah, safety flag
  (zelená/žlutá/červená), signály BUY_ZONE / DIVIDEND_TRAP; REITY posuzovány přes
  FFO/AFFO payout místo EPS payoutu. `git log --oneline | grep reit`
- **Valuace** — Gordon model + two-stage DDM (vysoký růst) + výnosová metoda,
  férová cena, nákupní zóna `[0.85·FV, FV]`, verdikt podhodnoceno/férové/přehodnoceno.
- **Split-adjustment dividend** — historické DPS škálováno podle pozdějších splitů;
  jinak by CAGR/yield u firem se splity nedávaly smysl.
- **Data vrstva (cache-first)** — SQLite DB, TTL fundamenty 72 h / dividendy 7 dní,
  FMP limit 250 volání/den s čítačem v `api_usage`, ceny vždy přes yfinance (quotes),
  denní price_history snapshoty → 52tídní statistiky (min/max, drawdown, percentil).
- **CLI** — `analyze` (i jako zkratka bez podpříkazu), `sync [--force --backfill]`,
  `scan` (zelené + BUY_ZONE; rc 0/2), `watch add/list/remove/export/import`,
  `portfolio add/remove/list/target/tax`, `alert add/list/check/remove`,
  `export watchlist|analysis` (CSV s BOM), `config show/set/unset`, `web`, `gui`.
- **Web GUI** — Apple-style, bez závislostí: srovnávací tabulka až 100 tickerů
  (seřaditelné sloupce, zelený řádek = v nákupní zóně), detailní karty se skóre,
  watchlist tabulka z DB, portfolio sekce (souhrn + cíl příjmu s progress barem),
  alerty (CRUD), FMP status bar, asynchronní joby (UI se nezavěšuje), tmavý režim.
- **Perzistentní reporty** — hotové analýzy se ukládají do DB a obnoví se po restartu
  GUI (`/api/reports`, DELETE na smazání). `3a08bc3`
- **Web GUI správa watchlisty + portfolia** — ★ toggle přímo v tabulce (přidat/odebrat
  sledování), formulář „Přidat do portfolia“ (ticker/počet/nákupní cena, auto i do
  watchlisty jako CLI), smazání pozice ✕, nastavení/odebrání cíle ročního příjmu.
  Nové endpointy: `POST|DELETE /api/watchlist[/<T>]`, `POST|DELETE /api/portfolio[/<T>]`,
  `POST /api/income-target`. Sledovaný ticker bez dat se zobrazí jako prázdný řádek s ★.
- **Scan v web GUI** — tlačítko „🎯 Najít nákupní příležitosti“ (ekvivalent CLI
  `divtrack scan`): asynchronní job přes `POST /api/scan`, filtr zelený flag + BUY_ZONE,
  prázdné pole = celá trvalá watchlista; výsledky jako klikací řádky (scroll k detailu).
- **Onboarding** — `./start.sh` (one-click: auto-install + web GUI), `install.sh`. `e80fea5`
- **Hygiena & CI** — ruff + mypy v CI (GitHub Actions, Python 3.10/3.12/3.13),
  MIT LICENSE, NAVOD.md (praktický návod). `d0dc42f`, `78d1c60`
- **Testy** — 270 testů, vše offline (domain + data layer s tmp DB + web API s mockem).

### Stav ověření (poslední kontrola)
```bash
.venv/bin/python -m pytest        # → 270 passed
.venv/bin/ruff check src tests    # → All checks passed!
.venv/bin/mypy                    # → Success: no issues found in 33 source files
git status                        # → clean (main)
```

---

## 3) Pravidla práce (z uživatele / Master Promptu)

1. **Komunikace VŽDY v češtině.**
2. Profesionální, kompletní implementace: kód + testy + commit (conventional commits).
3. Modular isolation — neměnit nesouvisející kód; SOLID; defenzivní programování
   (try/catch místo print()); strukturované logování.
4. **Před externími API voláními** (Yahoo Finance / FMP) se zeptat nebo nechat na
   uživateli — jednou byl live stah zamítnut. Testy musí být offline.
5. Uživatel používá app přes `./start.sh` a očekává end-to-end funkčnost s viditelnými
   daty; není zkušalý v server lifecycle → vysvětlovat srozumitelně + jeden konkrétní krok.
6. Po dokončení úkolu: testy zelené → commit → aktualizovat tento STAV.md.

---

## 4) Co se dělá právě teď (IN PROGRESS)

> Udržuj tuhle sekci vždy aktuální — co je v letu, kde jsme, jak pokračovat.

**Nic v letu** (2026-09-07) — hotovo: „web GUI správa watchlisty/portfolia“,
„cron noční sync“ (nainstalován do crontabu, 02:30) a „scan v web GUI“.
Další kandidát: **grafy** (backlog bod 4).

Pokračování po přerušení: přečíst tuto sekci, pak `git status` a `git log -3`.

---

## 5) Backlog / další kandidáty (seřazeno podle hodnoty pro uživatele)

1. ~~**Web GUI správa watchlisty + portfolia**~~ — hotovo 2026-09-07 (★ toggle, pozice, cíl příjmu)
2. ~~**Cron noční sync**~~ — hotovo 2026-09-07: `30 2 * * *` v crontabu, log do `~/.cache/dividend-tracker/sync.log` (ověřeno `crontab -l`)
3. ~~**`divtrack scan` do web GUI**~~ — hotovo 2026-09-07: tlačítko „🎯 Najít nákupní příležitosti“, `POST /api/scan`, klikací výsledky
4. **Grafy** — cena + dividendová historie v detailu karty (vanilla SVG, bez závislostí);
   data už jsou v DB (price_history, dividends_history)
5. **Rozšíření FMP fallback** — bez klíče běží jen yfinance; zvážit volitelný druhý provider
6. **`disallow_untyped_defs = true`** v mypy po Fázi 4+ (nyní postupné zavádění)
7. **UP (pyupgrade) ruff pravidla** — ~400 mechanických modernizací anotací jako samostatný commit

---

## 6) Log postupů (append-only, nejnovější nahoře)

### 2026-09-07 — hotovo „cron noční sync“ + „scan v web GUI“
- **Cron**: `30 2 * * * cd /home/ai/Prace/projekty/dividend-tracker && .venv/bin/python -m dividend_tracker.cli sync >> ~/.cache/dividend-tracker/sync.log 2>&1` nainstalován do crontabu (ověřeno `crontab -l`, cron daemon běží). Sync vyhodnocuje i alerty.
- **Scan v web GUI** (`web/server.py`): nový endpoint `POST /api/scan {tickers?}` → asynchronní job; bez tickers = celá trvalá watchlista (jako CLI); `JobManager.submit_scan()` analyzuje a `_finalize_scan()` do `job.extra.matches` uloží jen tickery se zeleným flagem + BUY_ZONE (stejný filtr jako `divtrack scan`). Import `SIGNAL_BUY_ZONE` z `domain.scoring`.
- Frontend: tlačítko „🎯 Najít nákupní příležitosti“ vedle Synchronizovat, nová sekce s klikacími výsledky (ticker, cena, férová, odstup k zóně, skóre) → scroll k detailní kartě; `setBusy` blokuje i scan tlačítko. CSS `.scan-row`.
- Testy: +4 nové offline testy v `tests/test_web_api.py` (`_fake_scan_report`, `_wait_matches`, fixture `scan_server`) — filtr matches, default na watchlistu, prázdná watchlista 400, neplatný ticker 400. Celkem **270 passed**, ruff clean, mypy clean (33 souborů), node --check OK.

### 2026-09-07 — hotovo „web GUI správa watchlisty/portfolia“
- `data/watchlist.py`: `build_watchlist_rows()` vrací `in_watchlist` flag; sledovaný
  ticker bez dat v DB se zobrazí jako prázdný řádek s ★ (placeholder).
- `web/server.py`: nové endpointy `POST|DELETE /api/watchlist[/<T>]`,
  `POST|DELETE /api/portfolio[/<T>]`, `POST /api/income-target`; sdílené helpery
  `_read_json_body` + `_valid_ticker` (400 s českou chybou). Záloha watchlisty se
  volá s explicitní cestou (`watchlist_store.DEFAULT_WATCHLIST_FILE`), aby testy
  mohly přesměrovat do tmp.
- Frontend: formulář „Přidat do portfolia“ + ✕ smazání pozice, input cíle ročního
  příjmu (+ odebrat), ★ toggle přímo v řádku watchlisty; nové CSS (.btn-primary,
  .wl-star-btn, styly formulářů). Sekce Portfolio se ukáže i jen s nastaveným cílem.
- Testy: +11 nových offline testů v `tests/test_web_api.py` (fixture `wl_backup_path`
  přesměrovává JSON zálohu do tmp). Celkem **266 passed**, ruff clean, mypy clean.

### 2026-09-07 — analýza stavu + start „web GUI správa watchlisty/portfolia“
- Přečteno: README, NAVOD, pyproject, web/server.py, data/*, cli.py, test_web_api.py.
- Ověřeno: 255 testů passed, ruff clean, mypy clean (33 souborů), git clean na main.
- Zjištěno: `master.zip` v `/home/ai/Prace/projekty/` NENÍ specifikace projektu
  (je to Ultimate Vocal Remover GUI) — neplést!
- Zjištěno: web API chybí write-endpointy pro watchlist/portfolio/cíl příjmu → úkol výše.
- Vytvořen tento STAV.md.

### 2026-09-04 (commity do `3a08bc3`)
- Perzistentní analytické reporty: DB tabulka reports, `/api/reports` GET/DELETE,
  GUI obnoví reporty po restartu; CSV export analýzy i watchlisty (BOM pro Excel).

### 2026-09-03 a dříve (výběr)
- `e80fea5` one-click `./start.sh` + install.sh
- `5e7843c` NAVOD.md — praktický návod
- `a3a87e5` DE withholding 26 %, výchozí watchlist path
- `d28acca`+`38abe10` `divtrack scan`, srovnávací tabulka hrubý→čistý yield po syncu
- `70acc68` paralelní fetch, backoff, disk cache TTL 1d, `--refresh/--verbose`
- `d0dc42f` ruff+mypy config, CI workflow, MIT LICENSE
- Fáze 1–5 Master Promptu: P0 opravy, REIT FFO/AFFO, two-stage DDM, split-adjustment,
  hygienický commit (ruff/mypy), paralelizace fetcherů.
