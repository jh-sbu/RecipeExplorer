import sqlite3

import pytest
from conftest import (
    BOLT,
    COPPER_BAR,
    ENCHANT,
    PATTERN_SHIRT,
    RACIAL_SPELL,
    SHIRT,
    SHIRT_SPELL,
    SMELT,
    SOD_ITEM,
    SOD_SPELL,
    THREAD,
    write_raw,
)

from recipe_explorer.ingest.normalize import SchemaError, build_gamedata


def test_professions_exclude_non_crafting_dnt_and_child_lines(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM professions ORDER BY name")]
    assert names == ["Enchanting", "Mining", "Tailoring"]


def test_skill_thresholds_land_in_the_right_columns(conn):
    r = conn.execute("SELECT * FROM recipes WHERE spell_id = ?", (SMELT,)).fetchone()
    assert (r["min_skill"], r["skill_yellow"], r["skill_green"], r["skill_grey"]) == (1, 25, 47, 70)
    assert r["product_item_id"] == COPPER_BAR


def test_first_ability_row_wins_for_duplicate_spells(conn):
    r = conn.execute("SELECT min_skill FROM recipes WHERE spell_id = ?", (SHIRT_SPELL,)).fetchone()
    assert r[0] == 40


def test_zero_base_points_means_one_item(conn):
    r = conn.execute("SELECT product_count FROM recipes WHERE spell_id = ?", (SHIRT_SPELL,)).fetchone()
    assert r[0] == 1


def test_reagents(conn):
    rows = conn.execute(
        "SELECT item_id, count FROM recipe_reagents WHERE spell_id = ? ORDER BY item_id", (SHIRT_SPELL,)
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted([(BOLT, 2), (THREAD, 1)])


def test_enchant_has_no_product_and_racial_is_excluded(conn):
    assert conn.execute("SELECT product_item_id FROM recipes WHERE spell_id = ?", (ENCHANT,)).fetchone()[0] is None
    assert conn.execute("SELECT 1 FROM recipes WHERE spell_id = ?", (RACIAL_SPELL,)).fetchone() is None


def test_missing_items_get_placeholders_and_flag_recipe_incomplete(conn):
    item = conn.execute("SELECT name, in_client FROM items WHERE id = ?", (SOD_ITEM,)).fetchone()
    assert tuple(item) == (f"Unknown item #{SOD_ITEM}", 0)
    assert conn.execute("SELECT complete FROM recipes WHERE spell_id = ?", (SOD_SPELL,)).fetchone()[0] == 0
    assert conn.execute("SELECT complete FROM recipes WHERE spell_id = ?", (SMELT,)).fetchone()[0] == 1


def test_recipe_teaches_and_view(conn):
    assert tuple(conn.execute("SELECT item_id, spell_id FROM recipe_teaches").fetchone()) == (PATTERN_SHIRT, SHIRT_SPELL)
    v = conn.execute("SELECT * FROM v_recipes WHERE spell_id = ?", (SHIRT_SPELL,)).fetchone()
    assert v["product"] == "Linen Shirt" and v["taught_by"] == "Pattern: Linen Shirt"
    assert "2x Bolt of Linen Cloth" in v["reagents"]


def test_manual_data_is_validated(conn, paths, capsys):
    assert [r[0] for r in conn.execute("SELECT item_id FROM vendor_items")] == [THREAD]
    assert paths["stats"]["vendor_items"] == 1
    assert conn.execute("SELECT source_type FROM recipe_sources WHERE spell_id = ?", (SHIRT_SPELL,)).fetchone()[0] == "trainer"


def test_meta_records_build(conn):
    assert conn.execute("SELECT value FROM meta WHERE key = 'build'").fetchone()[0] == "1.0.0.test"


def test_missing_column_fails_loudly_and_keeps_old_db(paths, manual_dir):
    sparse = paths["raw"] / "ItemSparse.csv"
    lines = sparse.read_text().splitlines()
    sparse.write_text("\n".join([lines[0].replace("Display_lang", "Name_lang")] + lines[1:]) + "\n")
    with pytest.raises(SchemaError, match="Display_lang"):
        build_gamedata(paths["raw"], paths["gamedata"], build="broken", manual_dir=manual_dir)
    # The previous DB is untouched.
    c = sqlite3.connect(paths["gamedata"])
    assert c.execute("SELECT value FROM meta WHERE key = 'build'").fetchone()[0] == "1.0.0.test"
    assert c.execute("SELECT count(*) FROM items WHERE id = ?", (SHIRT,)).fetchone()[0] == 1


def test_rebuild_is_repeatable(tmp_path, manual_dir):
    raw = tmp_path / "r"
    write_raw(raw)
    db = tmp_path / "g.sqlite"
    a = build_gamedata(raw, db, build="x", manual_dir=manual_dir)
    b = build_gamedata(raw, db, build="x", manual_dir=manual_dir)
    assert a == b
