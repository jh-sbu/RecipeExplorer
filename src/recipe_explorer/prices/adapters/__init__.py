"""Price source adapters.

Each adapter turns some external format (an addon's SavedVariables, an API response,
a CSV export...) into PriceSnapshot objects. To add a source, implement the
PriceAdapter protocol and register it in ADAPTERS.
"""

from recipe_explorer.prices.adapters.base import PriceAdapter
from recipe_explorer.prices.adapters.csv_adapter import CsvAdapter

ADAPTERS: dict[str, type[PriceAdapter]] = {
    CsvAdapter.name: CsvAdapter,
}

__all__ = ["ADAPTERS", "PriceAdapter"]
