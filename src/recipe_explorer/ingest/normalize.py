"""Turn raw wago.tools DB2 CSVs into the normalized gamedata SQLite schema.

All knowledge of raw DB2 column names lives in this module. Everything downstream
reads only the clean tables defined in sql/gamedata.sql.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from recipe_explorer.config import MANUAL_DIR, SQL_DIR

# Columns we depend on, per table. Checked up front so a renamed column in a new
# build fails loudly instead of silently producing empty tables.
REQUIRED_COLUMNS: dict[str, set[str]] = {
    "SkillLine": {"ID", "DisplayName_lang", "CategoryID", "ParentSkillLineID"},
    "SkillLineAbility": {
        "ID", "SkillLine", "Spell", "MinSkillLineRank", "TrivialSkillLineRankLow",
        "TrivialSkillLineRankHigh", "AcquireMethod", "NumSkillUps",
    },
    "SpellName": {"ID", "Name_lang"},
    "SpellEffect": {"SpellID", "Effect", "EffectIndex", "EffectItemType", "EffectBasePointsF"},
    "SpellReagents": {"SpellID"}
    | {f"Reagent_{i}" for i in range(8)}
    | {f"ReagentCount_{i}" for i in range(8)},
    "Item": {"ID", "ClassID", "SubclassID", "IconFileDataID"},
    "ItemSparse": {
        "ID", "Display_lang", "OverallQualityID", "ItemLevel", "RequiredLevel", "Stackable",
        "Bonding", "BuyPrice", "SellPrice", "VendorStackCount",
    },
    "ItemEffect": {"ID", "TriggerType", "SpellID"},
    "ItemXItemEffect": {"ItemEffectID", "ItemID"},
}

SKILL_CATEGORY = {"11": "primary", "9": "secondary"}
SPELL_EFFECT_CREATE_ITEM = "24"
ITEM_EFFECT_TRIGGER_LEARN = "6"
ITEM_CLASS_RECIPE = "9"


class SchemaError(RuntimeError):
    pass


def read_table(raw_dir: Path, table: str) -> Iterator[dict[str, str]]:
    path = raw_dir / f"{table}.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS[table] - set(reader.fieldnames or ())
        if missing:
            raise SchemaError(f"{path.name} is missing columns: {sorted(missing)}")
        yield from reader


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def build_gamedata(raw_dir: Path, db_path: Path, *, build: str, manual_dir: Path = MANUAL_DIR) -> dict[str, int]:
    """Build a fresh gamedata DB. Writes to a temp file and swaps it in atomically."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".sqlite.building")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript((SQL_DIR / "gamedata.sql").read_text())
        stats = _populate(conn, raw_dir, manual_dir)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("build", build),
                ("built_at", datetime.now(UTC).isoformat(timespec="seconds")),
                ("raw_dir", str(raw_dir)),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp_path, db_path)
    return stats


def _populate(conn: sqlite3.Connection, raw_dir: Path, manual_dir: Path) -> dict[str, int]:
    # --- candidate profession skill lines -------------------------------------
    skill_lines: dict[str, tuple[str, str]] = {}
    for r in read_table(raw_dir, "SkillLine"):
        category = SKILL_CATEGORY.get(r["CategoryID"])
        if category and r["ParentSkillLineID"] == "0" and "[DNT]" not in r["DisplayName_lang"]:
            skill_lines[r["ID"]] = (r["DisplayName_lang"], category)

    spell_names = {r["ID"]: r["Name_lang"] for r in read_table(raw_dir, "SpellName")}

    # --- what each spell creates and consumes ---------------------------------
    creates: dict[str, tuple[int, str, float]] = {}  # spell -> (effect_index, item, count)
    for r in read_table(raw_dir, "SpellEffect"):
        if r["Effect"] != SPELL_EFFECT_CREATE_ITEM or r["EffectItemType"] in ("", "0"):
            continue
        idx = int(r["EffectIndex"])
        if r["SpellID"] in creates and creates[r["SpellID"]][0] <= idx:
            continue
        # Older spells store 0 base points and a die roll; treat that as one item.
        count = max(float(r["EffectBasePointsF"] or 0), 1.0)
        creates[r["SpellID"]] = (idx, r["EffectItemType"], count)

    reagents: dict[str, list[tuple[str, int]]] = {}
    for r in read_table(raw_dir, "SpellReagents"):
        pairs = []
        for i in range(8):
            item, count = r[f"Reagent_{i}"], _int(r[f"ReagentCount_{i}"]) or 0
            if item not in ("", "0") and count > 0:
                pairs.append((item, count))
        if pairs:
            reagents[r["SpellID"]] = pairs

    # --- recipes: profession abilities that create an item or consume reagents -
    recipes: dict[str, dict] = {}
    used_professions: set[str] = set()
    for r in sorted(read_table(raw_dir, "SkillLineAbility"), key=lambda r: int(r["ID"])):
        spell, skill = r["Spell"], r["SkillLine"]
        if skill not in skill_lines or spell in recipes:
            continue
        if spell not in creates and spell not in reagents:
            continue
        yellow = _int(r["TrivialSkillLineRankLow"])
        grey = _int(r["TrivialSkillLineRankHigh"])
        green = (yellow + grey) // 2 if yellow and grey else None
        product = creates.get(spell)
        recipes[spell] = {
            "spell_id": int(spell),
            "name": spell_names.get(spell, f"Unknown spell #{spell}"),
            "profession_id": int(skill),
            "min_skill": _int(r["MinSkillLineRank"]),
            "skill_yellow": yellow,
            "skill_green": green,
            "skill_grey": grey,
            "acquire_method": _int(r["AcquireMethod"]),
            "num_skill_ups": _int(r["NumSkillUps"]),
            "product_item_id": int(product[1]) if product else None,
            "product_count": product[2] if product else None,
        }
        used_professions.add(skill)

    conn.executemany(
        "INSERT INTO professions(id, name, category) VALUES (?, ?, ?)",
        [(int(sid), *skill_lines[sid]) for sid in sorted(used_professions, key=int)],
    )

    # --- items ----------------------------------------------------------------
    item_base = {r["ID"]: r for r in read_table(raw_dir, "Item")}
    item_rows = {}
    for r in read_table(raw_dir, "ItemSparse"):
        base = item_base.get(r["ID"], {})
        item_rows[r["ID"]] = (
            int(r["ID"]),
            r["Display_lang"],
            _int(r["OverallQualityID"]),
            _int(r["ItemLevel"]),
            _int(r["RequiredLevel"]),
            _int(base.get("ClassID")),
            _int(base.get("SubclassID")),
            _int(r["Stackable"]),
            _int(r["Bonding"]),
            _int(r["BuyPrice"]),
            _int(r["SellPrice"]),
            _int(r["VendorStackCount"]),
            _int(base.get("IconFileDataID")),
            1,
        )

    # Recipes can reference items the client has no ItemSparse row for; keep FKs intact.
    referenced = {str(rec["product_item_id"]) for rec in recipes.values() if rec["product_item_id"] is not None}
    referenced |= {item for s in recipes for item, _ in reagents.get(s, [])}
    for item_id in referenced - item_rows.keys():
        base = item_base.get(item_id, {})
        item_rows[item_id] = (
            int(item_id), f"Unknown item #{item_id}", None, None, None,
            _int(base.get("ClassID")), _int(base.get("SubclassID")),
            None, None, None, None, None, _int(base.get("IconFileDataID")), 0,
        )

    conn.executemany(f"INSERT INTO items VALUES ({','.join('?' * 14)})", item_rows.values())
    _insert_dicts(conn, "recipes", list(recipes.values()))
    conn.executemany(
        "INSERT INTO recipe_reagents(spell_id, item_id, count) VALUES (?, ?, ?)",
        [(int(s), int(item), n) for s in recipes for item, n in reagents.get(s, [])],
    )

    # --- recipe items -> taught spell -----------------------------------------
    learn_effects = {
        r["ID"]: r["SpellID"]
        for r in read_table(raw_dir, "ItemEffect")
        if r["TriggerType"] == ITEM_EFFECT_TRIGGER_LEARN
    }
    teaches = set()
    for r in read_table(raw_dir, "ItemXItemEffect"):
        spell = learn_effects.get(r["ItemEffectID"])
        if (
            spell in recipes
            and r["ItemID"] in item_rows
            and item_base.get(r["ItemID"], {}).get("ClassID") == ITEM_CLASS_RECIPE
        ):
            teaches.add((int(r["ItemID"]), int(spell)))
    conn.executemany("INSERT INTO recipe_teaches(item_id, spell_id) VALUES (?, ?)", sorted(teaches))

    conn.execute("""
        UPDATE recipes SET complete = 0
         WHERE product_item_id IN (SELECT id FROM items WHERE NOT in_client)
            OR spell_id IN (SELECT rr.spell_id FROM recipe_reagents rr
                              JOIN items i ON i.id = rr.item_id WHERE NOT i.in_client)
    """)
    incomplete = conn.execute("SELECT count(*) FROM recipes WHERE NOT complete").fetchone()[0]

    manual = _load_manual(conn, manual_dir)

    return {
        "professions": len(used_professions),
        "items": len(item_rows),
        "recipes": len(recipes),
        "incomplete": incomplete,
        "recipe_reagents": sum(len(reagents.get(s, [])) for s in recipes),
        "recipe_teaches": len(teaches),
        **manual,
    }


def _insert_dicts(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", rows
    )


def _load_manual(conn: sqlite3.Connection, manual_dir: Path) -> dict[str, int]:
    """Load hand-maintained CSVs, validating IDs against the freshly built tables."""
    stats = {"vendor_items": 0, "recipe_sources": 0}

    vendor_path = manual_dir / "vendor_items.csv"
    if vendor_path.exists():
        with vendor_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                item_id = int(row["item_id"])
                found = conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
                if not found:
                    _warn(f"{vendor_path.name}: item {item_id} ({row.get('name')}) not in this build")
                    continue
                if row.get("name") and row["name"] != found[0]:
                    _warn(f"{vendor_path.name}: item {item_id} is {found[0]!r}, file says {row['name']!r}")
                conn.execute(
                    "INSERT OR REPLACE INTO vendor_items(item_id, notes) VALUES (?, ?)",
                    (item_id, row.get("notes") or None),
                )
                stats["vendor_items"] += 1

    sources_path = manual_dir / "recipe_sources.csv"
    if sources_path.exists():
        with sources_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                spell_id = int(row["spell_id"])
                if not conn.execute("SELECT 1 FROM recipes WHERE spell_id = ?", (spell_id,)).fetchone():
                    _warn(f"{sources_path.name}: recipe spell {spell_id} not in this build")
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO recipe_sources(spell_id, source_type, details) VALUES (?, ?, ?)",
                    (spell_id, row["source_type"], row.get("details") or ""),
                )
                stats["recipe_sources"] += 1

    return stats
