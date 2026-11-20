from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from recipe_explorer.prices.models import PriceSnapshot


def store_snapshots(
    conn: sqlite3.Connection,
    snapshots: Iterable[PriceSnapshot],
    *,
    source: str,
    realm: str,
    faction: str = "",
) -> int:
    """Insert snapshots into the prices DB. Re-importing the same scan is a no-op."""
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO price_snapshots
            (source, realm, faction, observed_at, item_id, min_buyout, market_value, quantity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (source, realm, faction.lower(), s.observed_at, s.item_id, s.min_buyout, s.market_value, s.quantity)
            for s in snapshots
        ),
    )
    conn.commit()
    return cur.rowcount
