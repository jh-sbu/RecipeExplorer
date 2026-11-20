"""Paths and constants shared across the package."""

from __future__ import annotations

import os
from pathlib import Path

# wago.tools product key for WoW Forever.
PRODUCT = "wowf"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("REX_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
GAMEDATA_DB = DATA_DIR / "gamedata.sqlite"
PRICES_DB = DATA_DIR / "prices.sqlite"

# Hand-maintained data that the client files don't contain (tracked in git).
MANUAL_DIR = PROJECT_ROOT / "manual"
SQL_DIR = Path(__file__).resolve().parent / "sql"


def raw_dir_for(build: str) -> Path:
    return RAW_DIR / build
