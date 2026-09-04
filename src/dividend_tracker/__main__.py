"""Vstupní bod pro ``python -m dividend_tracker`` (ekvivalent konzolového skriptu ``divtrack``)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
