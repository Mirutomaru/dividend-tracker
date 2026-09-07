# Návod k použití — Dividend Tracker

Praktický průvodce: jak aplikaci spustit a jak ji používat v běžné práci.
Architekturu a detaily schémat nalezneš v `README.md`; tady je jen „co stisknout“.

> ⚠️ Není investiční poradenství. Data pocházejí z veřejných zdrojů (Yahoo Finance,
> volitelně FMP) a mohou být nepřesná nebo opožděná. Kritická čísla ověř ve výroční
> zprávě společnosti.

---

## 1. Instalace a první spuštění — na jeden klik

Celá aplikace se spouští jedním příkazem. Při prvním běhu se vše samo nainstaluje:

```bash
cd /home/ai/Prace/projekty/dividend-tracker
./start.sh
```

Hotovo — web GUI se otevře v prohlížeči na `http://127.0.0.1:8742`.
Ukončíš Ctrl+C.

Co `./start.sh` dělá:
- **první běh**: sám vytvoří virtuální prostředí a nainstaluje závislosti (stačí mít `python3`)
- **další běhy**: rovnou spustí web GUI
- **jiný port**: `./start.sh --port 9000`
- **řádkové příkazy**: cokoliv dalšího se předá nástroji `divtrack`, např.
  `./start.sh analyze KO JNJ`, `./start.sh sync`, `./start.sh scan`

Jediná volitelná závislost: desktop GUI (`divtrack-gui`) vyžaduje Tk —
`sudo apt install python3-tk`. Webové GUI a CLI fungují bez něj plně.

> Pro vývojáře (testy, linter): `pip install -e ".[dev]"` ve venv `.venv`,
> nebo ručně: `./install.sh && .venv/bin/pip install -e ".[dev]"`.

---

## 2. Kde se ukládají data

| Co | Cesta | Poznámka |
|---|---|---|
| Databáze (SQLite) | `~/.cache/dividend-tracker/data.db` | primární úložiště — ceny, dividendy, watchlist, portfolio, alerty, nastavení |
| Záložní soubor watchlisty | `~/.config/dividend-tracker/watchlist.json` | lidsky čitelná záloha; obnoví se přes `divtrack watch import` |
| Konfigurace (`.env`) | `~/.cache/dividend-tracker/.env` | API klíče a přepsání cest |

Vše lze přepsat proměnnými prostředí (`DIVTRACK_DB`, `DIVTRACK_WATCHLIST`) nebo
příkazem `divtrack config set ...` (viz sekce 9).

---

## 3. Rychlý start — typický pracovní postup

Nejčastější sekvence pro běžné sledování dividendových akcií:

```bash
# 1) Přidej akcie do trvalé watchlisty
divtrack watch add KO JNJ MSFT

# 2) Synchronizuj data do lokální DB (ceny + fundamenta, cache-first)
divtrack sync

# 3) Zkontroluj, které akce jsou „zelené“ a v nákupní zóně
divtrack scan

# 4) Hloubková analýza konkrétní akcie (skóre, valuace, férová cena)
divtrack analyze KO
```

---

## 4. Příkazy podrobně

### `analyze` — hloubková analýza tickerů

Stáhne data a spočítá kompletní report: skóre 0–100, safety flag (zelená/žlutá/
červená), signály (`BUY_ZONE`, `DIVIDEND_TRAP`), férovou cenu a nákupní zónu.

```bash
divtrack analyze KO JNJ MSFT                 # více tickerů najednou
divtrack analyze KO --json report.json       # export reportu do JSON
divtrack analyze KO --required-return 9.5    # vlastní požadovaná výkonnost (Gordon)
divtrack analyze KO --no-benchmark           # přeskočit odvětvový benchmark (rychleji)
divtrack analyze KO --refresh                # ignorovat cache, stáhnout data znovu
```

Výstup: srovnávací tabulka + detailní blok pro každý ticker.

### `sync` — synchronizace dat do lokální DB

Cache-first stahování cen (yfinance) a fundamentů (FMP, pokud je nastaven klíč).
Bez argumentu se syncne vše z databáze; s argumenty jen zadané tickery.

```bash
divtrack sync                 # syncne vše z DB
divtrack sync KO JNJ          # jen tyto tickery
divtrack sync --force         # ignorovat TTL, stáhnout znovu
divtrack sync --backfill      # doplnit historii cen za 1 rok (52tídní percentil/drawdown)
```

Po `sync` se v konzoli zobrazí i srovnávací tabulka s hrubým a čistým (po zdanění)
yieldem.

### `scan` — vyhledat nejlepší kandidáty na nákup

Vyfiltruje akcie, které mají **zelený safety flag** A signál **BUY_ZONE**. Zdroj
tickerů: pozicní argumenty > soubor `--file` > trvalá watchlista z DB.

```bash
divtrack scan                          # prohledá celou watchlistu z DB
divtrack scan KO JNJ MSFT              # jen tyto tickery
divtrack scan --file tickers.txt       # tickery ze souboru (jeden na řádek, '#' = komentář)
divtrack scan --refresh                # čerstvá data
```

Příklad `tickers.txt`:

```text
# moje dividendové akcie
KO
JNJ
BRK.B
MSFT
```

Exit code: `0` = hotovo (i když žádná shoda), `2` = chyba (neplatný vstup, neexistující
soubor). Vhodné do skriptů a cronu.

### `watch` — správa trvalé watchlisty

Watchlista je trvalá (SQLite + JSON záloha) a nikdy se neztratí.

```bash
divtrack watch add KO JNJ              # přidat tickery
divtrack watch add KO --note "kralovna dividend"   # s poznámkou
divtrack watch list                    # výpis
divtrack watch remove MSFT             # odebrat
divtrack watch export                  # záloha DB → JSON (výchozí cesta)
divtrack watch import                  # obnova JSON → DB (idempotentní, slučuje)
```

### `portfolio` — držby a cíl ročního příjmu

Sleduje, co skutečně držíš (počet akcií, nákupní cena), spočítá roční příjem,
celkový yield i čistý příjem po zdanění.

```bash
divtrack portfolio add KO --shares 100 --cost 50.0        # přidat pozici
divtrack portfolio list                                   # výpis + příjem
divtrack portfolio remove MSFT                            # odebrat pozici
divtrack portfolio target --amount 24000                  # nastavit cíl ročního příjmu
divtrack portfolio target                                 # zobrazit aktuální stav/cíl

# sazby srážkové daně (přepsání vestavěných sazeb)
divtrack portfolio tax list                               # aktuální sazby
divtrack portfolio tax set --country DE 15                # přepsat DE na 15 %
divtrack portfolio tax remove --country DE                # vrátit vestavěnou sazbu
```

### `alert` — upozornění na cenu / nákupní zónu

Pravidla se vyhodnocují proti cenám v lokální DB (offline).

```bash
divtrack alert add KO --condition below --threshold 58     # upozorni, když cena < 58
divtrack alert add KO --condition above --threshold 70     # upozorni, když cena > 70
divtrack alert add KO --condition buy_zone                 # upozorni ve férové zóně (výnosová)
divtrack alert list                                        # výpis pravidel
divtrack alert check                                       # vyhodnotit všechna pravidla nyní
divtrack alert remove --id <ID>                            # smazat pravidlo
```

### `export` — CSV export

```bash
divtrack export watchlist                 # srovnávací řádky přímo z DB (offline) → watchlist.csv
divtrack export analysis KO JNJ           # hloubkové reporty → analysis.csv
divtrack export watchlist --file mój.csv  # vlastní výstupní cesta
```

### `config` — konfigurace (.env)

Spravuje proměnné v `.env`. Povolené klíče: `FMP_API_KEY`, `DIVTRACK_DB`,
`DIVTRACK_WATCHLIST`.

```bash
divtrack config show                          # zobrazit aktuální konfiguraci
divtrack config set FMP_API_KEY=xxxx          # nastavit API klíč (pro fundamenta)
divtrack config unset FMP_API_KEY             # odebrat
```

Bez `FMP_API_KEY` běží aplikace plně na yfinance — fundamenty se stahují pomaleji,
ale funkční. Klíč je volitelný.

---

## 5. Grafické rozhraní

### Webové GUI (doporučeno)

Moderní, čistý web v prohlížeči — pracuje i přímo z lokální databáze.
Nejjednodušší cesta: `./start.sh` (viz sekce 1). Ručně:

```bash
divtrack web                    # spustí na http://127.0.0.1:8742
divtrack web --port 9000        # jiný port
```

Otevři v prohlížeči `http://127.0.0.1:8742`. Zastavíš Ctrl+C.

Co umí přímo v prohlížeči (bez CLI):
- **★ ve watchlistě** — kliknutím na hvězdičku přidáš/odebereš ticker do trvalé
  watchlisty; nad tabulkou je i tlačítko „+ Sledovat“. Sledovaný ticker, který ještě
  nemá data (nesynchronizovaný), se zobrazí jako prázdný řádek s ★.
- **Portfolio** — formulář „Přidat pozici“ (ticker, počet akcií, nákupní cena)
  přidá/aktualizuje drženou pozici a automaticky ji i do watchlisty; ✕ na konci
  řádku pozici smaže. Sekce ukazuje souhrn držeb a cíl ročního příjmu s progress barem.
- **Cíl ročního příjmu** — zadej částku a „Uložit cíl“ (tlačítko „Odebrat cíl“ ho
  smaže); portfolio pak ukazuje, kolik % cíle aktuální dividendy pokrývají.
- **🎯 Najít nákupní příležitosti** — ekvivalent CLI `divtrack scan`: analyzuje tickery
  z pole (nebo celou trvalou watchlistu, pokud je prázdné) a ukáže jen ty se zeleným
  bezpečnostním flagem a signálem BUY_ZONE. Kliknutím na výsledek skočíš k detailní kartě.

### Tkinter GUI (desktop)

```bash
divtrack-gui                    # nebo: divtrack gui
```

V horní liště zadej ticker (např. `KO`), Enter / „Přidat a analyzovat“. Vlevo roste
seznam sledovaných, vpravo srovnávací tabulka; kliknutím na řádek se otevře detailní
report. Tlačítko „Export JSON…“ uloží všechny reporty do souboru.

---

## 6. Časté scénáře

**„Chci vědět, co z mé watchlisty stojí za koupi teď.“**
```bash
divtrack sync && divtrack scan
```

**„Přidám novou akcii a chci ji rovnou analyzovat.“**
```bash
divtrack watch add PFE && divtrack analyze PFE
```

**„Chci vědět, kolik mi dividendy přinášejí čistě po zdanění.“**
```bash
divtrack portfolio list
```

**„Chci upozornění, když KO klesne pod 58 $.“**
```bash
divtrack alert add KO --condition below --threshold 58
# pravidlo se vyhodnotí při dalším syncu nebo ručně:
divtrack alert check
```

**„Záloha watchlisty, abych ji neztratil.“**
```bash
divtrack watch export        # → ~/.config/dividend-tracker/watchlist.json
```

---

## 7. Spouštění v pozadí / cron (noční sync)

Doporučený noční běh: každé ráno synchronizovat data a zkontrolovat alerty.

```bash
# ručně jednou, ověřit že funguje:
divtrack sync && divtrack alert check

# do crontabu (cron -e):
30 2 * * * cd /home/ai/Prace/projekty/dividend-tracker && .venv/bin/python -m dividend_tracker.cli sync >> ~/.cache/dividend-tracker/sync.log 2>&1
```

> Pozn.: `divtrack` je console script; v cronu je spolehlivější volat modul přímo
> přes `.venv/bin/python -m dividend_tracker.cli`.

---

## 8. Řešení problémů

| Problém | Řešení |
|---|---|
| `command not found: divtrack` | Aktivuj venv (`source .venv/bin/activate`) nebo použij `.venv/bin/divtrack` / `python -m dividend_tracker` |
| GUI se nespustí (chybí Tk) | `sudo apt install python3-tk` |
| Pomalý první stah dat | Normální — data se cachují na disk (TTL 1 den). Další běh je rychlejší. `--refresh` vynutí nový stah. |
| Chyby od providera / rate-limit | Aplikace má vestavěné retry s backoffem; zkus znovu později nebo `sync --force`. |
| Web se nespustí na portu | Port je obsazený — použij jiný: `divtrack web --port 9000` |
| Chci čisté data od začátku | Vymaž DB: `rm ~/.cache/dividend-tracker/data.db*` (watchlist záloha zůstane v `~/.config/...`) |

---

## 9. Rychlá referenční tabulka

```text
divtrack analyze KO JNJ          hloubková analýza + skóre + férová cena
divtrack sync                    stáhnout data do lokální DB (cache-first)
divtrack scan                    najít zelené akcie v nákupní zóně
divtrack watch add/list/remove   správa trvalé watchlisty
divtrack portfolio list          držby, roční příjem, čistý yield
divtrack alert check             vyhodnotit upozornění na cenu/zónu
divtrack export watchlist        CSV export watchlisty (offline)
divtrack config show             zobrazit konfiguraci (.env)
divtrack web                     webové GUI na http://127.0.0.1:8742
divtrack-gui                     desktopové Tkinter GUI
```
