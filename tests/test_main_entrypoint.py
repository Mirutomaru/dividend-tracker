"""Testy vstupního bodu ``python -m dividend_tracker``."""

import subprocess
import sys


def test_module_entrypoint_help() -> None:
    """``python -m dividend_tracker --help`` se spustí a vrací 0."""
    result = subprocess.run(
        [sys.executable, "-m", "dividend_tracker", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "usage: divtrack" in result.stdout
