from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """One observed AH price for one item. All money values are copper per unit."""

    item_id: int
    observed_at: str  # ISO-8601 UTC
    min_buyout: int | None = None
    market_value: int | None = None
    quantity: int | None = None

    @property
    def best_estimate(self) -> int | None:
        """Price to use when a single number is needed."""
        return self.market_value if self.market_value is not None else self.min_buyout
