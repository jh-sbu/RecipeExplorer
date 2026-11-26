-- Static game data, rebuilt from scratch for each client build.
-- Never store anything here that can't be regenerated (see prices.sql for that).

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE professions (
    id       INTEGER PRIMARY KEY,          -- SkillLine.ID
    name     TEXT NOT NULL,
    category TEXT NOT NULL                 -- 'primary' | 'secondary'
);

CREATE TABLE items (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    quality            INTEGER,            -- 0 poor .. 5 legendary
    item_level         INTEGER,
    required_level     INTEGER,
    class_id           INTEGER,
    subclass_id        INTEGER,
    stack_size         INTEGER,
    bonding            INTEGER,            -- 0 none, 1 BoP, 2 BoE, 3 BoU, 4 quest
    buy_price          INTEGER,            -- copper; only meaningful if a vendor actually sells it
    sell_price         INTEGER,            -- copper, vendor pays you
    vendor_stack_count INTEGER,
    icon_file_id       INTEGER,
    in_client          INTEGER NOT NULL DEFAULT 1  -- 0 = placeholder: referenced but no ItemSparse row
);
CREATE INDEX items_name ON items(name);

CREATE TABLE recipes (
    spell_id         INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    profession_id    INTEGER NOT NULL REFERENCES professions(id),
    min_skill        INTEGER,              -- SkillLineAbility.MinSkillLineRank (1 for nearly every recipe: not the learn rank)
    -- Skill needed to learn the recipe: RequiredSkillRank of the item that teaches it
    -- (lowest, if several do), or 1 if it comes with the profession (acquire_method 1).
    -- NULL for trainer-taught recipes: the client doesn't hold those.
    learn_rank       INTEGER,
    skill_yellow     INTEGER,              -- TrivialSkillLineRankLow
    skill_grey       INTEGER,              -- TrivialSkillLineRankHigh
    skill_green      INTEGER,              -- conventional midpoint of yellow and grey
    acquire_method   INTEGER,              -- 1 = learned with the skill line, 0/2/3 = trained/taught
    num_skill_ups    INTEGER,
    product_item_id  INTEGER REFERENCES items(id),   -- NULL for enchants and other non-item crafts
    product_count    REAL,                 -- items created per cast
    -- 0 if the product or a reagent has no ItemSparse row in this client. Those are almost
    -- always Season of Discovery or removed content left in the client files.
    complete         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX recipes_profession ON recipes(profession_id);
CREATE INDEX recipes_product ON recipes(product_item_id);

CREATE TABLE recipe_reagents (
    spell_id INTEGER NOT NULL REFERENCES recipes(spell_id),
    item_id  INTEGER NOT NULL REFERENCES items(id),
    count    INTEGER NOT NULL,
    PRIMARY KEY (spell_id, item_id)
);
CREATE INDEX recipe_reagents_item ON recipe_reagents(item_id);

-- Recipe/pattern/plan/schematic items -> the recipe spell they teach.
CREATE TABLE recipe_teaches (
    item_id  INTEGER NOT NULL REFERENCES items(id),
    spell_id INTEGER NOT NULL REFERENCES recipes(spell_id),
    PRIMARY KEY (item_id, spell_id)
);
CREATE INDEX recipe_teaches_spell ON recipe_teaches(spell_id);

-- Server-side knowledge the client lacks; loaded from manual/*.csv.
CREATE TABLE recipe_sources (
    spell_id    INTEGER NOT NULL,
    source_type TEXT NOT NULL,             -- trainer | vendor | drop | quest | world | other
    details     TEXT,
    PRIMARY KEY (spell_id, source_type, details)
);

CREATE TABLE vendor_items (
    item_id INTEGER PRIMARY KEY REFERENCES items(id),
    notes   TEXT
);

CREATE VIEW v_recipes AS
SELECT r.spell_id,
       r.name,
       p.name          AS profession,
       r.min_skill,
       r.learn_rank,
       r.skill_yellow,
       r.skill_green,
       r.skill_grey,
       r.product_item_id,
       i.name          AS product,
       i.quality       AS product_quality,
       r.product_count,
       r.complete,
       (SELECT group_concat(rr.count || 'x ' || ri.name, ', ')
          FROM recipe_reagents rr JOIN items ri ON ri.id = rr.item_id
         WHERE rr.spell_id = r.spell_id) AS reagents,
       (SELECT group_concat(ti.name, ', ')
          FROM recipe_teaches t JOIN items ti ON ti.id = t.item_id
         WHERE t.spell_id = r.spell_id) AS taught_by
  FROM recipes r
  JOIN professions p ON p.id = r.profession_id
  LEFT JOIN items i ON i.id = r.product_item_id;

CREATE VIEW v_reagent_usage AS
SELECT rr.item_id     AS reagent_id,
       ri.name        AS reagent,
       rr.count,
       r.spell_id,
       r.name         AS recipe,
       p.name         AS profession,
       r.skill_yellow,
       r.complete
  FROM recipe_reagents rr
  JOIN items ri      ON ri.id = rr.item_id
  JOIN recipes r     ON r.spell_id = rr.spell_id
  JOIN professions p ON p.id = r.profession_id;
