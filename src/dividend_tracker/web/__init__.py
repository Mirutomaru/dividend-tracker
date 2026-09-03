"""Webové GUI — lehký HTTP server na standardní knihovně (bez nových závislostí).

Spustí se přes ``divtrack web``. Frontend je statická trojice souborů
(index.html / style.css / app.js) v ``web/static/``, backend nabízí malou
JSON API pro asynchronní analýzu tickerů.
"""

from .server import JobManager, WebApp, create_app, main  # noqa: F401
