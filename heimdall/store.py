"""Heimdall's store: state files and config.toml. The alerts and incidents live in Mimir (heimdall/_mimir.py).

Paths are module attributes read at call time, so a test points them at a scratch directory
(`store.ROOT`, `store.STATE`, `store.CONFIG`, `store.ALERTS_NOW`) the way
Fafnir's tests do. Stdlib only: imported by Jarvis's venv and Heimdall's own.
"""
from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("HEIMDALL_HOME", Path.home() / ".sentinel"))
STATE = ROOT / "state"
SOURCE = "heimdall"  # the prefix of every record kind Heimdall writes in Mimir
CONFIG = ROOT / "config.toml"
log = logging.getLogger("heimdall")


def load_config() -> dict:
    """~/.sentinel/config.toml; missing means every module's own defaults."""
    try:
        with open(CONFIG, "rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}


# Where `alerts-now` writes. config.toml `alerts_now` points it at the file Jarvis's readers already
# know (~/.jarvis/ALERTS-NOW.md); without a config it is Heimdall's own state (tests).
ALERTS_NOW = Path(str(load_config().get("alerts_now") or STATE / "ALERTS-NOW.md")).expanduser()
