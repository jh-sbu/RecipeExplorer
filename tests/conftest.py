"""A tiny synthetic DB2 dataset, written with exactly the columns normalize requires."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from recipe_explorer.db import open_gamedata, open_prices
from recipe_explorer.ingest.normalize import REQUIRED_COLUMNS, build_gamedata

# Item IDs
COPPER_ORE, COPPER_BAR = 2770, 2840
LINEN, BOLT, THREAD, SHIRT = 2589, 2996, 2320, 2577
PATTERN_SHIRT = 7000
LESSER_ESS, GREATER_ESS = 10938, 10939
SOD_ITEM = 222222  # referenced by a recipe but absent from ItemSparse

# Spell IDs
SMELT, BOLT_SPELL, SHIRT_SPELL = 2657, 2963, 3915
ESS_UP, ESS_DOWN = 13362, 13361
ENCHANT = 7418
SOD_SPELL = 446226
RACIAL_SPELL = 20589

ITEMS = {
    # id: (name, quality, buy_price, vendor_stack_count, class_id)
    COPPER_ORE: ("Copper Ore", 1, 20, 1, 7),
    COPPER_BAR: ("Copper Bar", 1, 40, 1, 7),
    LINEN: ("Linen Cloth", 1, 55, 1, 7),
    BOLT: ("Bolt of Linen Cloth", 1, 100, 1, 7),
    THREAD: ("Coarse Thread", 1, 50, 5, 7),  # 10c each from a vendor
    SHIRT: ("Linen Shirt", 1, 300, 1, 4),
    PATTERN_SHIRT: ("Pattern: Linen Shirt", 1, 400, 1, 9),
    LESSER_ESS: ("Lesser Magic Essence", 2, 0, 1, 7),
    GREATER_ESS: ("Greater Magic Essence", 2, 0, 1, 7),
}

SKILL_LINES = [
    # ID, name, category, parent
    ("186", "Mining", "11", "0"),
    ("197", "Tailoring", "11", "0"),
    ("333", "Enchanting", "11", "0"),
    ("754", "Racial - Human", "9", "0"),
    ("2933", "Test Profession [DNT]", "11", "0"),
    ("2946", "Mining", "11", "186"),  # child line, ignored
]

ABILITIES = [
    # ID, skill line, spell, min, yellow, grey
    (1, "186", SMELT, 1, 25, 70),
    (2, "197", BOLT_SPELL, 1, 25, 50),
    (3, "197", SHIRT_SPELL, 40, 60, 100),
    (4, "333", ESS_UP, 10, 30, 50),
    (5, "333", ESS_DOWN, 10, 30, 50),
    (6, "333", ENCHANT, 1, 70, 110),
    (7, "197", SOD_SPELL, 200, 220, 240),
    (8, "754", RACIAL_SPELL, 0, 0, 0),
    (9, "197", SHIRT_SPELL, 99, 99, 99),  # duplicate row: first by ID wins
]

CREATES = {  # spell: (item, count)
    SMELT: (COPPER_BAR, 1),
    BOLT_SPELL: (BOLT, 1),
    SHIRT_SPELL: (SHIRT, 0),  # 0 base points -> treated as 1
    ESS_UP: (GREATER_ESS, 1),
    ESS_DOWN: (LESSER_ESS, 3),
    SOD_SPELL: (SOD_ITEM, 1),
}

REAGENTS = {
    SMELT: [(COPPER_ORE, 1)],
    BOLT_SPELL: [(LINEN, 2)],
    SHIRT_SPELL: [(BOLT, 2), (THREAD, 1)],
    ESS_UP: [(LESSER_ESS, 3)],
    ESS_DOWN: [(GREATER_ESS, 1)],
    ENCHANT: [(LESSER_ESS, 1)],
    SOD_SPELL: [(LINEN, 1)],
}


def _write(raw: Path, table: str, rows: list[dict]) -> None:
    cols = sorted(REQUIRED_COLUMNS[table])
    with (raw / f"{table}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "0") for c in cols})


def write_raw(raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    _write(raw, "SkillLine", [
        {"ID": i, "DisplayName_lang": n, "CategoryID": c, "ParentSkillLineID": p} for i, n, c, p in SKILL_LINES
    ])
    _write(raw, "SkillLineAbility", [
        {"ID": i, "SkillLine": sl, "Spell": sp, "MinSkillLineRank": mn, "TrivialSkillLineRankLow": y,
         "TrivialSkillLineRankHigh": g, "AcquireMethod": 0, "NumSkillUps": 1}
        for i, sl, sp, mn, y, g in ABILITIES
    ])
    names = {SMELT: "Smelt Copper", BOLT_SPELL: "Bolt of Linen Cloth", SHIRT_SPELL: "Linen Shirt",
             ESS_UP: "Greater Magic Essence", ESS_DOWN: "Lesser Magic Essence",
             ENCHANT: "Enchant Bracer - Minor Health", SOD_SPELL: "Flask of Nightmares",
             RACIAL_SPELL: "Diplomacy"}
    _write(raw, "SpellName", [{"ID": k, "Name_lang": v} for k, v in names.items()])
    _write(raw, "SpellEffect", [
        {"SpellID": s, "Effect": 24, "EffectIndex": 0, "EffectItemType": item, "EffectBasePointsF": n}
        for s, (item, n) in CREATES.items()
    ] + [{"SpellID": ENCHANT, "Effect": 53, "EffectIndex": 0, "EffectItemType": 0, "EffectBasePointsF": 0}])
    rows = []
    for spell, pairs in REAGENTS.items():
        row = {"SpellID": spell}
        for i, (item, n) in enumerate(pairs):
            row[f"Reagent_{i}"], row[f"ReagentCount_{i}"] = item, n
        rows.append(row)
    _write(raw, "SpellReagents", rows)
    _write(raw, "Item", [{"ID": i, "ClassID": v[4], "SubclassID": 0} for i, v in ITEMS.items()])
    _write(raw, "ItemSparse", [
        {"ID": i, "Display_lang": n, "OverallQualityID": q, "BuyPrice": bp, "VendorStackCount": vs,
         "SellPrice": bp // 4, "Stackable": 20}
        for i, (n, q, bp, vs, _) in ITEMS.items()
    ])
    _write(raw, "ItemEffect", [{"ID": 500, "TriggerType": 6, "SpellID": SHIRT_SPELL}])
    _write(raw, "ItemXItemEffect", [{"ItemEffectID": 500, "ItemID": PATTERN_SHIRT}])


@pytest.fixture
def manual_dir(tmp_path: Path) -> Path:
    d = tmp_path / "manual"
    d.mkdir()
    (d / "vendor_items.csv").write_text(f"item_id,name,notes\n{THREAD},Coarse Thread,\n424242,Ghost Item,\n")
    (d / "recipe_sources.csv").write_text(f"spell_id,source_type,details\n{SHIRT_SPELL},trainer,Any tailoring trainer\n")
    return d


@pytest.fixture
def paths(tmp_path: Path, manual_dir: Path) -> dict[str, Path]:
    raw = tmp_path / "raw" / "test"
    write_raw(raw)
    gamedata, prices = tmp_path / "gamedata.sqlite", tmp_path / "prices.sqlite"
    stats = build_gamedata(raw, gamedata, build="1.0.0.test", manual_dir=manual_dir)
    return {"raw": raw, "gamedata": gamedata, "prices": prices, "stats": stats, "manual": manual_dir}


@pytest.fixture
def conn(paths):
    c = open_gamedata(paths["gamedata"], prices_path=paths["prices"])
    yield c
    c.close()


@pytest.fixture
def prices_conn(paths):
    c = open_prices(paths["prices"])
    yield c
    c.close()
