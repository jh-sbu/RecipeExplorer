from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from recipe_explorer.prices.models import PriceSnapshot


def _opt_int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None


class CsvAdapter:
    """Generic CSV: item_id, min_buyout, market_value, quantity, observed_at (optional).

    Money columns are copper per unit. Useful for hand-entered prices and as a
    lowest-common-denominator target for other tools' exports.
    """

    name: ClassVar[str] = "csv"

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Iterator[PriceSnapshot]:
        default_time = datetime.fromtimestamp(self.path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if "item_id" not in (reader.fieldnames or ()):
                raise ValueError(f"{self.path}: CSV needs an item_id column")
            for row in reader:
                yield PriceSnapshot(
                    item_id=int(row["item_id"]),
                    observed_at=row.get("observed_at") or default_time,
                    min_buyout=_opt_int(row.get("min_buyout")),
                    market_value=_opt_int(row.get("market_value")),
                    quantity=_opt_int(row.get("quantity")),
                )
