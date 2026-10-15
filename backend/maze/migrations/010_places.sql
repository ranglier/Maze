-- Lieux : arborescence libre (région > ville > quartier > bâtiment…), reliés aux PNJ et factions.

CREATE TABLE places (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    -- Sous-lieu de ; à la suppression d'un lieu, l'API remonte ses sous-lieux d'un niveau.
    parent_id   INTEGER REFERENCES places(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'lieu',
    summary     TEXT NOT NULL DEFAULT '',
    ambiance    TEXT NOT NULL DEFAULT '',   -- texte à lire aux joueurs
    description TEXT NOT NULL DEFAULT '',
    secrets     TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    image_id    INTEGER REFERENCES images(id) ON DELETE SET NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_places_campaign ON places (campaign_id);
CREATE INDEX ix_places_parent ON places (parent_id);

-- PNJ ou faction présent dans un lieu, avec son rôle (« Vit ici », « Siège »…).
CREATE TABLE place_links (
    id          INTEGER PRIMARY KEY,
    place_id    INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('npc', 'faction')),
    entity_id   INTEGER NOT NULL,
    role        TEXT NOT NULL DEFAULT '',
    UNIQUE (place_id, entity_kind, entity_id)
);
CREATE INDEX ix_place_links_entity ON place_links (entity_kind, entity_id);

-- Images : les lieux en ont aussi (propriétaire « place »). SQLite ne sait pas modifier une
-- contrainte CHECK : la table est reconstruite (clés étrangères suspendues par db.migrate).
DROP TRIGGER trg_npcs_delete;
DROP TRIGGER trg_factions_delete;
CREATE TABLE images_new (
    id            INTEGER PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    owner_kind    TEXT NOT NULL CHECK (owner_kind IN ('npc', 'faction', 'place')),
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
INSERT INTO images_new SELECT id, campaign_id, owner_kind, owner_id, file, thumb, original_name, width, height,
                              caption, sort_order, created_at FROM images;
DROP TABLE images;
ALTER TABLE images_new RENAME TO images;
CREATE INDEX ix_images_owner ON images (owner_kind, owner_id);

-- Nettoyage des références polymorphes à la suppression d'un PNJ, d'une faction ou d'un lieu.
CREATE TRIGGER trg_npcs_delete AFTER DELETE ON npcs BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'npc' AND source_id = OLD.id)
        OR (target_kind = 'npc' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'npc' AND owner_id = OLD.id;
    DELETE FROM place_links WHERE entity_kind = 'npc' AND entity_id = OLD.id;
END;

CREATE TRIGGER trg_factions_delete AFTER DELETE ON factions BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'faction' AND source_id = OLD.id)
        OR (target_kind = 'faction' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'faction' AND owner_id = OLD.id;
    DELETE FROM place_links WHERE entity_kind = 'faction' AND entity_id = OLD.id;
END;

CREATE TRIGGER trg_places_delete AFTER DELETE ON places BEGIN
    DELETE FROM images WHERE owner_kind = 'place' AND owner_id = OLD.id;
    DELETE FROM document_mentions WHERE target_kind = 'place' AND target_id = OLD.id;
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 5;
END;

-- Recherche globale (rowid = identifiant * 8 + 5).
CREATE TRIGGER trg_search_place_insert AFTER INSERT ON places BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.ambiance || ' ' || NEW.description || ' ' || NEW.secrets || ' ' || NEW.notes
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_place_update AFTER UPDATE OF name, summary, ambiance, description, secrets, notes, tags
    ON places BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 5;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.ambiance || ' ' || NEW.description || ' ' || NEW.secrets || ' ' || NEW.notes
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;

-- Mentions « place » enregistrées avant l'existence des lieux : sans objet.
DELETE FROM document_mentions WHERE target_kind = 'place';

-- Le champ « Lieu » des PNJ (texte libre, jamais affiché jusqu'ici) devient un vrai lieu
-- (PNJ orphelins, d'une campagne supprimée hors de Maze, ignorés).
INSERT INTO places (campaign_id, name)
SELECT DISTINCT campaign_id, trim(location) FROM npcs
 WHERE trim(location) <> '' AND campaign_id IN (SELECT id FROM campaigns);
INSERT OR IGNORE INTO place_links (place_id, entity_kind, entity_id, role)
SELECT p.id, 'npc', n.id, 'Se trouve ici'
  FROM npcs n JOIN places p ON p.campaign_id = n.campaign_id AND p.name = trim(n.location)
 WHERE trim(n.location) <> '';
