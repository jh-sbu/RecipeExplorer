from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Protocol

from recipe_explorer.prices.models import PriceSnapshot


class PriceAdapter(Protocol):
    """Reads one price source and yields snapshots.

    Realm/faction are supplied by the caller rather than the adapter, because many
    sources (CSV exports, single-realm addon scans) don't record them reliably.
    """

    name: ClassVar[str]

    def __init__(self, path: Path) -> None: ...

    def read(self) -> Iterator[PriceSnapshot]: ...
