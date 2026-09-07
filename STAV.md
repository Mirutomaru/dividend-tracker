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
│   ├── metrics.py       metriky A (dividendy), B (valuace vč. P/B, Graham, Lynch PEG), C (solvence)
│   ├── scoring.py       skóre 0–100 + safety flag + signály (BUY_ZONE / DIVIDEND_TRAP)
│   ├── valuation.py     Gordon, two-stage DDM, výnosová metoda, nákupní zóna
│   ├── portfolio_returns.py  TWR (chain-linking) + IRR (bisekce) — čisté funkce, cash flow konvence buy=−/sell+dividend=+
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
| GET | `/api/history/<TICKER>` | historie cen (365 dní) + výplat pro grafy v detailní kartě (offline z DB) |
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
- **Grafy v detailní kartě** — vanilla SVG bez závislostí: line chart ceny za 365 dní
  + sloupcový graf posledních 12 výplat; lazy-load přes `GET /api/history/<TICKER>`
  (MutationObserver zachytí karty ze všech cest — analýza, scan, uložené reporty).
- **Valuační metriky + TWR/IRR + tooltipy** — detailní karta: nové tile P/B, Graham
  číslo (s marží bezpečí), Peter Lynch PEG (5letý CAGR dividend jako proxy růstu) a
  DCF rozklad férové ceny (Gordon/two-stage/výnosová). Srovnávací tabulka: +5 sortable
  sloupců (P/E, P/B, EV/EBITDA, Graham, Lynch PEG). Portfolio souhrn: TWR % p.a. a
  IRR % p.a. z nové transakční historie (`portfolio_transactions`, automaticky plněná
  při změnách pozic v `upsert_position`; čistý modul `domain/portfolio_returns.py`).
  Každá metrika má vysvětlovku pod kurzorem myši (plovoucí tooltip přes `data-tip`).
- **Onboarding** — `./start.sh` (one-click: auto-install + web GUI), `install.sh`. `e80fea5`
- **Hygiena & CI** — ruff + mypy v CI (GitHub Actions, Python 3.10/3.12/3.13),
  MIT LICENSE, NAVOD.md (praktický návod). `d0dc42f`, `78d1c60`
- **Testy** — 292 testů, vše offline (domain + data layer s tmp DB + web API s mockem).

### Stav ověření (poslední kontrola)
```bash
.venv/bin/python -m pytest        # → 314 passed
.venv/bin/ruff check src tests    # → All checks passed!
.venv/bin/mypy                    # → Success: no issues found in 35 source files
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
„cron noční sync“, „scan v web GUI“, **grafy**, **valuační metriky + TWR/IRR + tooltipy**,
oprava nočního syncu (skript přesunut do `~/.hermes/profiles/developer/scripts/`,
`divtrack sync` bez argů teď zahrnuje i trvalou watchlistu), FMP fallback (Tiingo)
a **mypy `disallow_untyped_defs = true`** (posledních 4 chybějících anotací doplněno).
**Tiingo ověřeno proti živému API** — klíč nastaven, opraveny 3 odchylky od specifikace
(auth Token ne Bearer, ceny na `/prices`, dividendy z `divCash` v barech); live sync KO
napsal 40 dividendových záznamů. **UP (pyupgrade) ruff pravidla** — 515 mechanických
modernizací anotací ve 26 souborech, UP zapnuto v config. **Celý backlog (8 bodů) hotový.**
Další kandidáti: viz backlog níže (nové položky po dokončení původních).

Pokračování po přerušení: přečíst tuto sekci, pak `git status` a `git log -3`.

---

## 5) Backlog / další kandidáty (seřazeno podle hodnoty pro uživatele)

1. ~~**Web GUI správa watchlisty + portfolia**~~ — hotovo 2026-09-07 (★ toggle, pozice, cíl příjmu)
2. ~~**Cron noční sync**~~ — hotovo 2026-09-07: `30 2 * * *` v crontabu, log do `~/.cache/dividend-tracker/sync.log` (ověřeno `crontab -l`)
3. ~~**`divtrack scan` do web GUI**~~ — hotovo 2026-09-07: tlačítko „🎯 Najít nákupní příležitosti“, `POST /api/scan`, klikací výsledky
4. ~~**Grafy**~~ — hotovo 2026-09-07: vanilla SVG line chart ceny (365 dní) + sloupcový graf výplat v detailní kartě, lazy-load přes `GET /api/history/<TICKER>`
5. ~~**Valuační metriky + TWR/IRR + tooltipy**~~ — hotovo 2026-09-07: P/B, Graham číslo, Peter Lynch PEG, DCF rozklad v detailní kartě; srovnávací tabulka +5 sortable sloupců (P/E, P/B, EV/EBITDA, Graham, Lynch PEG); portfolio TWR % p.a. + IRR % p.a. z transakční historie (`portfolio_transactions`); vysvětlovky pod kurzorem myši na všech metrikách
6. ~~**Rozšíření FMP fallback**~~ — hotovo 2026-09-07: druhý provider Tiingo (10 000 volání/den), chain FMP → Tiingo → yfinance, ceny primárně Tiingo; **ověřeno proti živému API** (klíč nastaven, opraveny odchylky od specifikace — viz log níže)
7. ~~**`disallow_untyped_defs = true`** v mypy~~ — hotovo 2026-09-07: doplněny poslední 4 chybějící anotace (`data/watchlist.py`, `gui/app.py`), příznak přepnut na true
8. ~~**UP (pyupgrade) ruff pravidla**~~ — hotovo 2026-09-07: 515 mechanických modernizací ve 26 souborech (`Optional[X]`→`X | None`, `List/Dict/Tuple`→`list/dict/tuple`, `Iterator`→`collections.abc`), UP zapnuto v ruff config
9. **GitHub remote + push** — CI workflow (`.github/workflows/ci.yml`: ruff + mypy + pytest na matici 3.10/3.12/3.13) už existuje, ale repo nemá `git remote` → pipeline se nikdy nespustila; stačí přidat remote a pushnout
10. **Dividendový kalendář bez FMP** — Tiingo z `divCash` umí jen historii; buď odhad příští výplaty z frekvence, nebo `/distributions` endpoint (beta plán)
11. **Noční sync s `--backfill`** — `divtrack_nightly_sync.sh` volá `divtrack sync` bez flagu `--backfill`, takže 52tídní percentil/drawdown + TWR/IRR se doplní jen ručně; přidat `--backfill` do nočního spouštění

---

## 6) Log postupů (append-only, nejnovější nahoře)

### 2026-09-07 — Ukliděno na další práci + odstraněna duplicita nočního syncu
- **Duplicitní crontab**: user `crontab` měl vlastní záznam `divtrack sync` na 02:30, který se překrýval s Hermes cron jobem `divtrack-nightly-sync` (stejný čas) → zítra by se sync spustil dvakrát (dvojnásobné API volání + závod na SQLite). User crontab obsahoval jen tuhle řádku, takže nahrazen komentářem; kanonický je Hermes cron job (má doručení zprávy + ošetření chyb).
- **Noční sync dnes v 02:31 selhal** „Script not found“ — skript `divtrack_nightly_sync.sh` byl vytvořen až 14:19, tedy po běhu. Dnes v noci už poběží (skript existuje, syntaxe OK).
- **Stav na odchozí**: git čistý na `main`, web server běží na aktuálním kódu (`/api/health` ok), Tiingo klíč nastaven (1/10000 volání), 314 testů zelených, ruff + mypy clean. Původní backlog 8/8 hotový, nové kandidáty 9–11.

### 2026-09-07 — UP (pyupgrade) ruff pravidla (backlog bod 8 hotovo)
- **Motivace**: příznak byl záměrně vypnutý „~400 mechanických modernizací jako samostatný commit“; po dokončení ostatních backlog bodů na něj přišlo.
- **Změny** (515 ve 26 souborech, čistě mechanické): `Optional[X]` → `X | None` (UP045/UP007), `List/Dict/Tuple/Set` → `list/dict/tuple/set` (UP006), `Iterator` přesunut z `typing` do `collections.abc` (UP037), vyčištěny nevyužité typing importy (F401) a opraveno řazení importů (I001).
- **Konfigurace**: `"UP"` přidáno do `[tool.ruff.lint] select` — modernizace se teď udržuje; starý komentář „záměrně VYPNUTO“ odstraněn.
- **Backlog doplněn** o 3 nové kandidáty (9–11): GitHub remote + push (CI workflow už existuje, jen chybí remote), dividendový kalendář bez FMP, noční sync s `--backfill`.
- **Ověření**: ruff clean (s UP zapnutým), mypy clean (`mypy` i `mypy src` jako v CI), **314 passed**. Commit `51342b7`.

### 2026-09-07 — Tiingo ověřeno proti živému API + oprava 3 odchylek od specifikace
- **Motivace**: uživatel dodal `TIINGO_API_KEY` (uloženo přes `divtrack config set` do `~/.cache/dividend-tracker/.env`). Live test KO prozradil, že implementace podle OpenAPI spec se liší od reálného chování API.
- **Auth**: Tiingo očekává `Authorization: Token <klíč>`, ne `Bearer` (Bearer → 403 „Please supply a token“). Opraveno v `_get_json`; nová regresní test hlavičky (`test_auth_header_uses_token_scheme`).
- **Ceny**: `/daily/{T}` s datovými parametry vrací META místo barů — bary jsou na `/daily/{T}/prices?startDate=...&endDate=...`. Předtím se ceny ticho padaly do yfinance fallbacku (Tiingo „fungoval“ ale nic nedaroval).
- **Meta**: exchange je v poli `exchangeCode` (ne `exchange`), měna v metadu vůbec není → doplní výchozí hodnota.
- **Dividendy**: `/corporate-actions/{T}/dividends` = 404 a `/distributions` = 403 (beta/enterprise plán). Na free plánu se historie odvozuje z pole `divCash` v cenových barech — nová metoda `get_dividend_history()` (1 volání, celá historie; bez parametrů Tiingo vrátí vše). Budoucí výplaty se z historie nedají odvodit → kalendář bývá prázdný.
- **Testová izolace**: `test_no_api_key_uses_yfinance_fallback` selhával v plném běhu — testy CLI/web při importu načtou reálný `.env` (s Tiingo klíčem) a chain ho správně preferoval; opraveno `monkeypatch.delenv("TIINGO_API_KEY")`.
- **Live ověření**: KO → name „Coca-Cola Company“, exchange NYSE, 40 dividendových záznamů (2016–2026) s korektními částkami.
- **Ověření**: **314 passed** (+2 nové testy), ruff clean, mypy clean. Commit `26fadd3`.

### 2026-09-07 — mypy `disallow_untyped_defs = true` (backlog bod 7 hotovo)
- **Motivace**: příznak byl vypnutý „postupné zavádění“; kontrolou se ukázalo, že zbývá jen 4 chybějících anotací ve 2 souborech — dokončit bylo levnější než nechat viset.
- **Opravy**: `data/watchlist.py` — `_build_series() -> Optional[pd.Series]` (pandas import pod `TYPE_CHECKING`, runtime import v těle zůstal), `_ttm(series: Optional[pd.Series])`. `gui/app.py` — `_tree_find() -> str | None`, `_on_tree_select(_event: tk.Event | None = None)`.
- **Přepnutí**: `[tool.mypy] disallow_untyped_defs = true` v pyproject.toml.
- **Ověření**: mypy clean (35 souborů), ruff clean, **312 passed**.

### 2026-09-07 — watchlist tabulka: záložky pohledů + sloučené buňky + typografie
- **Motivace**: 14 sloupců vedle sebe se na běžném monitoru bez ořezání nevejde; čísla nesedly pod sebou.
- **Záložky nad tabulkou** (`index.html` + `app.js`): fixní sloupce ★ Ticker / Název (sloučené) / Sektor / Cena+Změna (sloučené) jsou vždy viditelné; zbytek se přepíná: **Dividendy** (DPS TTM, Yield %, Net yield %, 5R růst, Série let, Příští div.), **Cena a volatilita** (Objem, 52t rozsah, Drawdown %), **Vše / Vlastní** (vše + dropdown „Sloupce“ — zapnutí/vypnutí libovolného sloupce, stav v `localStorage`).
- **Sloučené dvouřádkové buňky**: ticker+název (název drobně tlumeně pod), cena+změna (absolutní + % změna barevně pod cenou), 52t rozsah = mini range-bar (tečka na lince mezi min/max) + `$lo – $hi` + percentil 52t, příští dividenda = datum nahoře / částka pod.
- **Typografie a zarovnání**: `font-variant-numeric: tabular-nums` na celé tabulce; text doleva, čísla/částky/procenta doprava (včetně záhlaví), data a ikony na střed; zkrácené popisky záhlaví bez zalamování („5R růst“, „Příští div.“).
- **Vizuál**: padding řádků 10px 14px, `border-bottom` mezi řádky, hover `#1a1a1a` (dark) / jemný šedý (light), sektor jako šedý badge/pilulka.
- **Implementace**: sloupce definovány datově (`WL_COLS` + `WL_TABS`) — render hlavičky i řádků z jedné definice; seřazení přes delegovaný listener na `<thead>` (hlavička se s pohledem překresluje); sloučené sloupce mají `sortKey` (seřadí podle primárního údaje).
- **Ověření**: Playwright headless — 3 záložky přepínají správně, dropdown Sloupce skryje/zobrazí sloupec (13→12), seřazení funguje, computed style: číselné sloupce `text-align: right` + `tabular-nums`, žádná JS chyba. Celkem **312 passed**, ruff clean, mypy clean.

### 2026-09-07 — Tiingo jako druhý data provider (ceny primárně, dividendy v chainu)
- **Motivace**: Yahoo Finance má přísné limity synchronizace; Tiingo nabízí 10 000 volání/den.
- **Nový modul** `data/tiingo.py` — klient s Bearer auth (`TIINGO_API_KEY`), denní čítač v `api_usage` (limit přepisovatelný `TIINGO_DAILY_LIMIT`), endpointy: daily OHLCV, corporate-actions/dividends, meta tickeru. Čistý urllib, žádné nové závislosti.
- **Provider chain** v `sync.py`: fundamenty + dividendy jdou **FMP → Tiingo → yfinance**; každý provider jen pokud je nakonfigurovaný a má limit; při chybě/limitu pokračuje řetězec dál. `sync_via_tiingo` = 1 volání na ticker (dividendy), meta jen při prvním syncu tickera.
- **Ceny** (`quotes.py`): primárně Tiingo (aktuální cena z posledních 10 dní, historie s `adjClose`), fallback yfinance pro chybějící tickery; bez klíče se chová jako dříve (čistý yfinance batch).
- **CLI**: `divtrack config set TIINGO_API_KEY=...`, výstup sync tiskne i „Tiingo: X/10000 volání dnes“.
- **Web GUI**: nový status bar Tiingo (`GET /api/tiingo-status`) vedle FMP; po syncu se oba limity obnoví.
- **Testy**: +18 offline testů v `tests/test_tiingo.py` (klient, limit/429/401, normalizace dividend s rozdělením historie/kalendář, chain FMP→Tiingo→yfinance, ceny a backfill přes Tiingo). Celkem **312 passed**, ruff clean, mypy clean.
- **Dokumentace**: NAVOD.md (config), README.md (provider chain + konfigurace).

### 2026-09-07 — oprava nočního syncu + restart web serveru na aktuální kód
- **Diagnóza**: Hermes cron job `divtrack-nightly-sync` selhával 4× po sobě (od 04. 9.) chybou „Script not found: ~/.hermes/profiles/developer/scripts/divtrack_nightly_sync.sh“ — skript ležel v `~/.hermes/scripts/` (default profil), ale job se řeší relativně vůči profilemu adresáři. Druhá chyba: `divtrack sync` bez argů čte jen tabulku `stocks`; JNJ/KO jsou jen v trvalé watchlistě → „Databáze je prázdná“ (exit 2). Třetí: web server běžel od 09:59 s kódem starším než commity z 11:18/13:03 (`/api/history` a `/api/scan` vracely 404).
- **Opravy**: (1) skript zkopírován do `~/.hermes/profiles/developer/scripts/divtrack_nightly_sync.sh`; (2) `cli.py cmd sync` — bez argů se teď sbírá sjednocení `list_stocks()` + `list_watchlist()`; nový test `test_sync_without_args_includes_watchlist`. Celkem **293 passed**, ruff clean, mypy clean.
- **Restart**: web server (port 8742) spuštěn znovu s aktuálním kódem — ověřeno `/api/health` 200, `/api/history/KO` 200, `POST /api/scan` → job_id.
- **Poznámka pro uživatele**: DB je prázdná (poslední data z 4. 9.) — po nočním syncu se naplní JNJ + KO; alternativně v GUI tlačítko „Synchronizovat“.

### 2026-09-07 — hotovo „valuační metriky + TWR/IRR + tooltipy“
- **Backend**: `models.py` — `MetricsReport` dostal `p_b`, `graham_number`, `lynch_peg`. `domain/metrics.py` — výpočet P/B (cena ÷ equity/shares), Graham číslo √(22,5·EPS_TTM·BVPS) a Peter Lynch PEG = P/E ÷ 5letý CAGR dividend (proxy růstu). Nová tabulka `portfolio_transactions` v `db.py` + CRUD (`add_transaction`/`list_transactions`/`clear_transactions`); `upsert_position` automaticky zapisuje buy/sell transakce při změně pozice (prodej za poslední známou cenou z quotes).
- **Nový modul** `domain/portfolio_returns.py` — čisté funkce bez sítě i DB: `time_weighted_return(value_series, external_flows)` (chain-linking TWR v %) a `internal_rate_of_return(cashflows, terminal_value, end_date)` (IRR p.a. bisekcí). Konvence cash flow: buy = záporné, sell/dividend = kladné.
- **`data/portfolio.py`** — `compute_portfolio_returns(db)`: skládá hodnotový řetězec portfolia z price_history + transakcí + dividend (držené akcie × DPS na ex-date); `build_portfolio_report` vrací `twr_pct`/`irr_pct` v souhrnu.
- **Frontend** (`app.js`, `index.html`, `style.css`): tooltip systém — plovoucí div `#metric-tooltip` (position: fixed) + JS event delegace na `data-tip` (zvoleno místo CSS ::after, protože `.ticker-card`/`.table-card` mají overflow:hidden). Slovník `METRIC_TIPS` (~20 českých vysvětlovek), `tile()` rozšířen o tipKey. Detailní karta: nové tile P/B, Graham číslo (s marží bezpečí), Lynch PEG + DCF rozklad férové ceny (Gordon/two-stage/výnosová). Srovnávací tabulka: +5 sortable sloupců (P/E, P/B, EV/EBITDA, Graham, Lynch PEG) s data-tip na záhlavích. Portfolio souhrn: TWR % p.a. + IRR % p.a.
- **Testy**: +19 nových offline testů — `test_metrics.py` (+6: P/B=2.0×, Graham=$90.00, Lynch PEG=0.88 na fixture; edge cases), nový `tests/test_portfolio_returns.py` (8 testů čistých funkcí TWR/IRR), `test_portfolio.py` (+4 end-to-end přes DB: prázdné→None, nákup za rok→TWR≈+20 % i IRR≈+20 %, auto-transakce v upsert_position, dividendový cash flow), `test_web_api.py` (+1: /api/portfolio vystavuje twr_pct/irr_pct). Celkem **292 passed**, ruff clean, mypy clean (34 souborů), node --check OK.

### 2026-09-07 — hotovo „grafy v detailní kartě“
- `web/server.py`: nový endpoint `GET /api/history/<TICKER>` → `{ticker, prices: [{date, close}], dividends: [{ex_date, amount}]}`; ceny = denní snapshoty za 365 dní (nejstarší první), dividendy = celá historie výplat (nejstarší první); neplatný ticker → 400.
- Frontend (`app.js`): vanilla SVG bez závislostí — `priceChartSvg()` (line chart + area fill, mřížka s popisky max/střed/min, tečka na aktuální ceně) a `dividendChartSvg()` (sloupcový graf posledních 12 výplat s hodnotami). Lazy-load: `loadCardCharts(ticker)` fetchne `/api/history/<T>` a vykreslí; `_chartsLoaded` Set chrání proti dvojnásobnému fetchi.
- Napojení: `observeChartCards()` — MutationObserver na `resultsEl` zachytí vklad karet ze všech cest (analýza, scan, uložené reporty) a spustí lazy-load; volá se v init před `loadStoredReports()`.
- CSS: `.chart`, `.chart-line/area/dot/bar/grid/axis/val/empty` — barevné proměnné z design systému (accent/green), funguje i v tmavém režimu.
- Testy: +3 nové offline testy v `tests/test_web_api.py` (fixture `history_server` s 3 cenami + 4 výplatami) — pořadí a obsah, prázdná historie pro neznámý ticker, neplatný ticker 400. Celkem **273 passed**, ruff clean, mypy clean (33 souborů), node --check OK.

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
