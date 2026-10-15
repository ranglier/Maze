-- Identifiants jamais réutilisés (audit M5 = DATA-8) : SQLite redonnait le numéro de la dernière ligne supprimée
-- (id INTEGER PRIMARY KEY sans AUTOINCREMENT). Une fiche supprimée puis une autre créée : les mentions de
-- l'ancienne (data-mention="npc:N" dans le texte des scénarios et des champs longs), ses illustrations
-- (data-doc-image), ses commentaires ancrés (data-comment), la carte d'un scénario et un éditeur resté hors
-- ligne se rattachaient en silence à la nouvelle. Tables reconstruites avec AUTOINCREMENT : celles dont l'identifiant
-- est cité dans du contenu ou par un couple (type, id) : campaigns, npcs, factions, places, documents (mentions,
-- relations, lieux, carte des scénarios, temps réel), images (galeries, portrait, export Foundry), maps,
-- document_images (illustrations des fiches rédigées), comments (ancres des commentaires).
-- Procédure de SQLite pour reconstruire une table (clés étrangères suspendues par db.migrate, vérifiées avant de
-- valider) : nouvelle table, copie, suppression de l'ancienne, renommage ; puis ses index et déclencheurs
-- recréés à l'identique (recherche plein texte, nettoyage des références à la suppression, révision de la
-- migration 025), vérifié par tests.test_migrations (sqlite_master avant et après). Les numéros déjà libérés
-- avant cette migration au-dessus du plus grand existant peuvent encore resservir une fois ; plus aucun ensuite.
--
-- Index des clés étrangères (audit PERF-2) : la suppression d'une image parcourait npcs, factions et places en
-- entier (ON DELETE SET NULL sans index), d'où une suppression de campagne quadratique ; suppression d'un compte
-- (user_id), filtres par campagne et recherche d'une image par fichier.

-- Renommage à l'ancienne : les déclencheurs d'autres tables qui citent une table reconstruite (recherche des
-- commentaires et des fiches rédigées) ne sont ni vérifiés ni réécrits pendant qu'elle est absente ; ils la
-- retrouvent sous son nom une fois renommée.
PRAGMA legacy_alter_table = ON;


-- campaigns
CREATE TABLE campaigns__autoinc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO campaigns__autoinc (
    id, name, description, created_at, updated_at)
SELECT
    id, name, description, created_at, updated_at
FROM campaigns;
DROP TABLE campaigns;
ALTER TABLE campaigns__autoinc RENAME TO campaigns;

-- npcs
CREATE TABLE npcs__autoinc (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
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
, sort_order INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0);
INSERT INTO npcs__autoinc (
    id, campaign_id, name, title, status, people, age, location, summary, appearance, personality,
    motivations, secrets, notes, tags, portrait_image_id, pos_x, pos_y, created_at, updated_at, sort_order,
    revision)
SELECT
    id, campaign_id, name, title, status, people, age, location, summary, appearance, personality,
    motivations, secrets, notes, tags, portrait_image_id, pos_x, pos_y, created_at, updated_at, sort_order,
    revision
FROM npcs;
DROP TABLE npcs;
ALTER TABLE npcs__autoinc RENAME TO npcs;
CREATE INDEX ix_npcs_campaign ON npcs (campaign_id);
CREATE TRIGGER trg_npcs_delete_mentions AFTER DELETE ON npcs BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'npc' AND target_id = OLD.id;
END;
CREATE TRIGGER trg_search_npc_delete AFTER DELETE ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
END;
CREATE TRIGGER trg_npcs_delete AFTER DELETE ON npcs BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'npc' AND source_id = OLD.id)
        OR (target_kind = 'npc' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'npc' AND owner_id = OLD.id;
    DELETE FROM place_links WHERE entity_kind = 'npc' AND entity_id = OLD.id;
END;
CREATE TRIGGER trg_npcs_delete_scenario AFTER DELETE ON npcs BEGIN
    DELETE FROM scenario_nodes WHERE entity_kind = 'npc' AND entity_id = OLD.id;
END;
CREATE TRIGGER trg_search_npc_insert AFTER INSERT ON npcs BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_npc_update AFTER UPDATE OF name, title, people, location, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_npcs_revision AFTER UPDATE OF name, title, status, people, age, location, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs
WHEN OLD.name IS NOT NEW.name OR OLD.title IS NOT NEW.title OR OLD.status IS NOT NEW.status
  OR OLD.people IS NOT NEW.people OR OLD.age IS NOT NEW.age OR OLD.location IS NOT NEW.location
  OR OLD.summary IS NOT NEW.summary OR OLD.appearance IS NOT NEW.appearance
  OR OLD.personality IS NOT NEW.personality OR OLD.motivations IS NOT NEW.motivations
  OR OLD.secrets IS NOT NEW.secrets OR OLD.notes IS NOT NEW.notes OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE npcs SET revision = revision + 1 WHERE id = NEW.id;
END;

-- factions
CREATE TABLE factions__autoinc (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
, sort_order INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0);
INSERT INTO factions__autoinc (
    id, campaign_id, name, kind, color, summary, description, goals, resources, secrets, notes, tags,
    emblem_image_id, pos_x, pos_y, created_at, updated_at, sort_order, revision)
SELECT
    id, campaign_id, name, kind, color, summary, description, goals, resources, secrets, notes, tags,
    emblem_image_id, pos_x, pos_y, created_at, updated_at, sort_order, revision
FROM factions;
DROP TABLE factions;
ALTER TABLE factions__autoinc RENAME TO factions;
CREATE INDEX ix_factions_campaign ON factions (campaign_id);
CREATE TRIGGER trg_factions_delete_mentions AFTER DELETE ON factions BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'faction' AND target_id = OLD.id;
END;
CREATE TRIGGER trg_search_faction_delete AFTER DELETE ON factions BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 2;
END;
CREATE TRIGGER trg_factions_delete AFTER DELETE ON factions BEGIN
    DELETE FROM relations
     WHERE (source_kind = 'faction' AND source_id = OLD.id)
        OR (target_kind = 'faction' AND target_id = OLD.id);
    DELETE FROM images WHERE owner_kind = 'faction' AND owner_id = OLD.id;
    DELETE FROM place_links WHERE entity_kind = 'faction' AND entity_id = OLD.id;
END;
CREATE TRIGGER trg_factions_delete_scenario AFTER DELETE ON factions BEGIN
    DELETE FROM scenario_nodes WHERE entity_kind = 'faction' AND entity_id = OLD.id;
END;
CREATE TRIGGER trg_search_faction_insert AFTER INSERT ON factions BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.goals) || ' ' || html_text(NEW.resources)
            || ' ' || html_text(NEW.secrets) || ' ' || html_text(NEW.notes) || ' ' || NEW.kind
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_faction_update AFTER UPDATE OF name, kind, summary, description, goals, resources,
    secrets, notes, tags ON factions BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 2;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.goals) || ' ' || html_text(NEW.resources)
            || ' ' || html_text(NEW.secrets) || ' ' || html_text(NEW.notes) || ' ' || NEW.kind
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_factions_revision AFTER UPDATE OF name, kind, color, summary, description, goals, resources,
    secrets, notes, tags ON factions
WHEN OLD.name IS NOT NEW.name OR OLD.kind IS NOT NEW.kind OR OLD.color IS NOT NEW.color
  OR OLD.summary IS NOT NEW.summary OR OLD.description IS NOT NEW.description OR OLD.goals IS NOT NEW.goals
  OR OLD.resources IS NOT NEW.resources OR OLD.secrets IS NOT NEW.secrets OR OLD.notes IS NOT NEW.notes
  OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE factions SET revision = revision + 1 WHERE id = NEW.id;
END;

-- places
CREATE TABLE places__autoinc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
, revision INTEGER NOT NULL DEFAULT 0);
INSERT INTO places__autoinc (
    id, campaign_id, parent_id, name, kind, summary, ambiance, description, secrets, notes, tags, image_id,
    sort_order, created_at, updated_at, revision)
SELECT
    id, campaign_id, parent_id, name, kind, summary, ambiance, description, secrets, notes, tags, image_id,
    sort_order, created_at, updated_at, revision
FROM places;
DROP TABLE places;
ALTER TABLE places__autoinc RENAME TO places;
CREATE INDEX ix_places_campaign ON places (campaign_id);
CREATE INDEX ix_places_parent ON places (parent_id);
CREATE TRIGGER trg_places_delete AFTER DELETE ON places BEGIN
    DELETE FROM images WHERE owner_kind = 'place' AND owner_id = OLD.id;
    DELETE FROM document_mentions WHERE target_kind = 'place' AND target_id = OLD.id;
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 5;
END;
CREATE TRIGGER trg_search_place_insert AFTER INSERT ON places BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.ambiance) || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_place_update AFTER UPDATE OF name, summary, ambiance, description, secrets, notes, tags ON places BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 5;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.ambiance) || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_places_revision AFTER UPDATE OF name, kind, summary, ambiance, description, secrets, notes, tags
    ON places
WHEN OLD.name IS NOT NEW.name OR OLD.kind IS NOT NEW.kind OR OLD.summary IS NOT NEW.summary
  OR OLD.ambiance IS NOT NEW.ambiance OR OLD.description IS NOT NEW.description OR OLD.secrets IS NOT NEW.secrets
  OR OLD.notes IS NOT NEW.notes OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE places SET revision = revision + 1 WHERE id = NEW.id;
END;

-- documents
CREATE TABLE documents__autoinc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('campaign', 'scenario', 'page')),
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'brouillon'
                CHECK (status IN ('brouillon', 'pret', 'en_cours', 'joue')),
    content     TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    -- Incrémenté à chaque enregistrement : détecte les modifications concurrentes.
    revision    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
, content_seq INTEGER NOT NULL DEFAULT 0, live_epoch INTEGER NOT NULL DEFAULT 0);
INSERT INTO documents__autoinc (
    id, campaign_id, parent_id, kind, title, status, content, sort_order, revision, created_at, updated_at,
    updated_by, content_seq, live_epoch)
SELECT
    id, campaign_id, parent_id, kind, title, status, content, sort_order, revision, created_at, updated_at,
    updated_by, content_seq, live_epoch
FROM documents;
DROP TABLE documents;
ALTER TABLE documents__autoinc RENAME TO documents;
CREATE INDEX ix_documents_campaign ON documents (campaign_id);
CREATE INDEX ix_documents_parent ON documents (parent_id);
CREATE UNIQUE INDEX ux_documents_root ON documents (campaign_id) WHERE kind = 'campaign';
CREATE TRIGGER trg_search_document_insert AFTER INSERT ON documents BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 3, 'document', NEW.id, NEW.campaign_id, NEW.id, NEW.title, html_text(NEW.content));
END;
CREATE TRIGGER trg_search_document_update AFTER UPDATE OF title, content ON documents BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 3;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 3, 'document', NEW.id, NEW.campaign_id, NEW.id, NEW.title, html_text(NEW.content));
END;
CREATE TRIGGER trg_search_document_delete AFTER DELETE ON documents BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 3;
END;
CREATE TRIGGER trg_documents_delete_links AFTER DELETE ON documents BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'doc' AND target_id = OLD.id;
END;

-- images
CREATE TABLE images__autoinc (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
INSERT INTO images__autoinc (
    id, campaign_id, owner_kind, owner_id, file, thumb, original_name, width, height, caption, sort_order,
    created_at)
SELECT
    id, campaign_id, owner_kind, owner_id, file, thumb, original_name, width, height, caption, sort_order,
    created_at
FROM images;
DROP TABLE images;
ALTER TABLE images__autoinc RENAME TO images;
CREATE INDEX ix_images_owner ON images (owner_kind, owner_id);

-- maps
CREATE TABLE maps__autoinc (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    place_id      INTEGER REFERENCES places(id) ON DELETE SET NULL,
    name          TEXT NOT NULL,
    file          TEXT NOT NULL,
    thumb         TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    width         INTEGER NOT NULL,
    height        INTEGER NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
, preview TEXT NOT NULL DEFAULT '', pin_style TEXT NOT NULL DEFAULT 'dot' CHECK (pin_style IN ('dot', 'icon', 'hidden')));
INSERT INTO maps__autoinc (
    id, campaign_id, place_id, name, file, thumb, original_name, width, height, sort_order, created_at,
    updated_at, preview, pin_style)
SELECT
    id, campaign_id, place_id, name, file, thumb, original_name, width, height, sort_order, created_at,
    updated_at, preview, pin_style
FROM maps;
DROP TABLE maps;
ALTER TABLE maps__autoinc RENAME TO maps;
CREATE INDEX ix_maps_campaign ON maps (campaign_id);
CREATE INDEX ix_maps_place ON maps (place_id);

-- document_images
CREATE TABLE document_images__autoinc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    file        TEXT NOT NULL,
    thumb       TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO document_images__autoinc (
    id, campaign_id, document_id, file, thumb, width, height, created_at)
SELECT
    id, campaign_id, document_id, file, thumb, width, height, created_at
FROM document_images;
DROP TABLE document_images;
ALTER TABLE document_images__autoinc RENAME TO document_images;
CREATE INDEX ix_document_images_document ON document_images (document_id);

-- comments
CREATE TABLE comments__autoinc (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',   -- conservé si le compte disparaît (ou à l'import)
    body        TEXT NOT NULL,
    quote       TEXT NOT NULL DEFAULT '',   -- extrait commenté, au moment de la création
    resolved    INTEGER NOT NULL DEFAULT 0,
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO comments__autoinc (
    id, document_id, parent_id, user_id, author_name, body, quote, resolved, resolved_by, resolved_at,
    created_at, updated_at)
SELECT
    id, document_id, parent_id, user_id, author_name, body, quote, resolved, resolved_by, resolved_at,
    created_at, updated_at
FROM comments;
DROP TABLE comments;
ALTER TABLE comments__autoinc RENAME TO comments;
CREATE INDEX ix_comments_document ON comments (document_id);
CREATE INDEX ix_comments_parent ON comments (parent_id);
CREATE TRIGGER trg_search_comment_insert AFTER INSERT ON comments BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 4, 'comment', NEW.id, (SELECT campaign_id FROM documents WHERE id = NEW.document_id),
            NEW.document_id, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_comment_update AFTER UPDATE OF body ON comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 4;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 4, 'comment', NEW.id, (SELECT campaign_id FROM documents WHERE id = NEW.document_id),
            NEW.document_id, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_comment_delete AFTER DELETE ON comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 4;
END;

PRAGMA legacy_alter_table = OFF;

-- Index des clés étrangères (PERF-2)
CREATE INDEX IF NOT EXISTS ix_npcs_portrait ON npcs (portrait_image_id);
CREATE INDEX IF NOT EXISTS ix_factions_emblem ON factions (emblem_image_id);
CREATE INDEX IF NOT EXISTS ix_places_image ON places (image_id);
CREATE INDEX IF NOT EXISTS ix_images_campaign ON images (campaign_id);
CREATE INDEX IF NOT EXISTS ix_images_file ON images (file);
CREATE INDEX IF NOT EXISTS ix_document_images_campaign ON document_images (campaign_id);
CREATE INDEX IF NOT EXISTS ix_documents_updated_by ON documents (updated_by);
CREATE INDEX IF NOT EXISTS ix_document_updates_user ON document_updates (user_id);
CREATE INDEX IF NOT EXISTS ix_comments_user ON comments (user_id);
CREATE INDEX IF NOT EXISTS ix_templates_user ON templates (user_id);
CREATE INDEX IF NOT EXISTS ix_foundry_tokens_created_by ON foundry_tokens (created_by);
CREATE INDEX IF NOT EXISTS ix_entry_versions_user ON entry_versions (user_id);
CREATE INDEX IF NOT EXISTS ix_entry_comments_user ON entry_comments (user_id);
