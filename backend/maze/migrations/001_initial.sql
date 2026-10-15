-- Schéma initial : campagnes, PNJ, factions, appartenances, relations, images.

CREATE TABLE campaigns (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Images rattachées à un PNJ ou une faction (propriétaire polymorphe).
CREATE TABLE images (
    id            INTEGER PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    owner_kind    TEXT NOT NULL CHECK (owner_kind IN ('npc', 'faction')),
    owner_id      INTEGER NOT NULL,
    file          TEXT NOT NULL,
    thumb         TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    width         INTEGER,
    height        INTEGER,
    caption       TEXT NOT NULL DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_images_owner ON images (owner_kind, owner_id);

CREATE TABLE factions (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT '',
    color           TEXT NOT NULL DEFAULT '#c9a45c',
    summary         TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    goals           TEXT NOT NULL DEFAULT '',
    resources       TEXT NOT NULL DEFAULT '',
    secrets         TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    emblem_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
    pos_x           REAL,
    pos_y           REAL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_factions_campaign ON factions (campaign_id);

CREATE TABLE npcs (
    id                INTEGER PRIMARY KEY,
    campaign_id       INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'vivant'
                      CHECK (status IN ('vivant', 'mort', 'disparu', 'inconnu')),
    people            TEXT NOT NULL DEFAULT '',
    age               TEXT NOT NULL DEFAULT '',
    location          TEXT NOT NULL DEFAULT '',
    summary           TEXT NOT NULL DEFAULT '',
    appearance        TEXT NOT NULL DEFAULT '',
    personality       TEXT NOT NULL DEFAULT '',
    motivations       TEXT NOT NULL DEFAULT '',
    secrets           TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '[]',
    portrait_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
    pos_x             REAL,
    pos_y             REAL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_npcs_campaign ON npcs (campaign_id);

CREATE TABLE memberships (
    id         INTEGER PRIMARY KEY,
    npc_id     INTEGER NOT NULL REFERENCES npcs(id) ON DELETE CASCADE,
    faction_id INTEGER NOT NULL REFERENCES factions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT '',
    is_leader  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (npc_id, faction_id)
);
CREATE INDEX ix_memberships_faction ON memberships (faction_id);

-- Relation entre deux entités quelconques (PNJ ou faction).
CREATE TABLE relations (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('npc', 'faction')),
    source_id   INTEGER NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('npc', 'faction')),
    target_id   INTEGER NOT NULL,
    type        TEXT NOT NULL DEFAULT 'autre',
    label       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    directed    INTEGER NOT NULL DEFAULT 0,
    secret      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (NOT (source_kind = target_kind AND source_id = target_id))
);
CREATE INDEX ix_relations_source ON relations (source_kind, source_id);
CREATE INDEX ix_relations_target ON relations (target_kind, target_id);
CREATE INDEX ix_relations_campaign ON relations (campaign_id);

-- Nettoyage des références polymorphes à la suppression d'un PNJ ou d'une faction.
CREATE TRIGGER trg_npcs_delete AFTER DELETE ON npcs BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'npc' AND source_id = OLD.id)
        OR (target_kind = 'npc' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'npc' AND owner_id = OLD.id;
END;

CREATE TRIGGER trg_factions_delete AFTER DELETE ON factions BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'faction' AND source_id = OLD.id)
        OR (target_kind = 'faction' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'faction' AND owner_id = OLD.id;
END;
