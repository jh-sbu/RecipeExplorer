"""Opening the two databases (static game data + long-lived price history)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from recipe_explorer.config import GAMEDATA_DB, PRICES_DB, SQL_DIR

PRICES_SCHEMA_VERSION = 1


def open_prices(path: Path = PRICES_DB) -> sqlite3.Connection:
    """Open (creating if needed) the price history DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript((SQL_DIR / "prices.sql").read_text())
    if conn.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (PRICES_SCHEMA_VERSION,))
        conn.commit()
    return conn


def open_gamedata(path: Path = GAMEDATA_DB, *, prices_path: Path | None = PRICES_DB) -> sqlite3.Connection:
    """Open game data read-only, with the price DB attached as schema `prices`.

    Tables are then reachable as e.g. `items` and `prices.price_snapshots`.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `rex build` first")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if prices_path is not None:
        open_prices(prices_path).close()  # ensure schema exists
        conn.execute("ATTACH DATABASE ? AS prices", (str(prices_path),))
    return conn
