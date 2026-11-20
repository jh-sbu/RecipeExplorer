"""Download client DB2 tables for WoW Forever from wago.tools as CSV."""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
from pathlib import Path

from recipe_explorer.config import PRODUCT, raw_dir_for

BASE_URL = "https://wago.tools"
USER_AGENT = "recipe-explorer/0.1 (local tool)"

# Every DB2 table the normalizer reads. Keep in sync with normalize.REQUIRED_COLUMNS.
TABLES = (
    "SkillLine",
    "SkillLineAbility",
    "SpellName",
    "SpellEffect",
    "SpellReagents",
    "Item",
    "ItemSparse",
    "ItemEffect",
    "ItemXItemEffect",
)


def _get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def list_builds(product: str = PRODUCT) -> list[str]:
    """Return build versions for the product, newest first."""
    builds = json.loads(_get(f"{BASE_URL}/api/builds"))
    if product not in builds:
        raise RuntimeError(f"wago.tools has no product {product!r}; known: {sorted(builds)}")
    return [b["version"] for b in builds[product]]


def latest_build(product: str = PRODUCT) -> str:
    return list_builds(product)[0]


def fetch_tables(build: str, *, force: bool = False, tables: tuple[str, ...] = TABLES) -> Path:
    """Download all required tables for a build into data/raw/<build>/. Returns that dir."""
    out_dir = raw_dir_for(build)
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in tables:
        dest = out_dir / f"{table}.csv"
        if dest.exists() and not force:
            continue
        print(f"  fetching {table} @ {build}")
        data = _get(f"{BASE_URL}/db2/{table}/csv?build={build}", timeout=180)
        if not data.strip() or data.lstrip().startswith(b"<"):
            raise RuntimeError(f"Unexpected response for {table} @ {build} (empty or HTML)")
        # Write atomically so an interrupted download never leaves a truncated CSV behind.
        with tempfile.NamedTemporaryFile(dir=out_dir, delete=False, suffix=".part") as tmp:
            tmp.write(data)
        shutil.move(tmp.name, dest)
    return out_dir
