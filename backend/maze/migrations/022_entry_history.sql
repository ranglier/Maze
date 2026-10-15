-- Historique des versions et commentaires des fiches PNJ, factions et lieux (panneau latéral, comme les
-- fiches rédigées). Une ligne appartient à exactement une fiche : npc_id, faction_id ou place_id ; les clés
-- étrangères suppriment versions et commentaires avec la fiche.

-- Versions : état des champs de texte de la fiche (JSON compressé zlib) après un enregistrement.
-- Automatiques (les enregistrements rapprochés d'un même auteur se fondent en une version), nommées
-- (manual) ou repères de restauration (restore). Ni images, ni liens, ni positions.
CREATE TABLE entry_versions (
    id          INTEGER PRIMARY KEY,
    npc_id      INTEGER REFERENCES npcs(id) ON DELETE CASCADE,
    faction_id  INTEGER REFERENCES factions(id) ON DELETE CASCADE,
    place_id    INTEGER REFERENCES places(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('auto', 'manual', 'restore')),
    label       TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',          -- nom de la fiche dans cette version
    content     BLOB NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    digest      TEXT NOT NULL DEFAULT '',
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK ((npc_id IS NOT NULL) + (faction_id IS NOT NULL) + (place_id IS NOT NULL) = 1)
);
CREATE INDEX ix_entry_versions_npc ON entry_versions (npc_id, id) WHERE npc_id IS NOT NULL;
CREATE INDEX ix_entry_versions_faction ON entry_versions (faction_id, id) WHERE faction_id IS NOT NULL;
CREATE INDEX ix_entry_versions_place ON entry_versions (place_id, id) WHERE place_id IS NOT NULL;

-- Commentaires : fils rattachés à une section de la fiche (field : appearance, secrets… ; vide = la fiche
-- entière) et à un extrait (quote), retrouvé dans le texte à l'affichage. Le contenu de la fiche n'est jamais
-- modifié par un commentaire.
CREATE TABLE entry_comments (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    npc_id      INTEGER REFERENCES npcs(id) ON DELETE CASCADE,
    faction_id  INTEGER REFERENCES factions(id) ON DELETE CASCADE,
    place_id    INTEGER REFERENCES places(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES entry_comments(id) ON DELETE CASCADE,
    field       TEXT NOT NULL DEFAULT '',
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    quote       TEXT NOT NULL DEFAULT '',
    resolved    INTEGER NOT NULL DEFAULT 0,
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK ((npc_id IS NOT NULL) + (faction_id IS NOT NULL) + (place_id IS NOT NULL) = 1)
);
CREATE INDEX ix_entry_comments_npc ON entry_comments (npc_id) WHERE npc_id IS NOT NULL;
CREATE INDEX ix_entry_comments_faction ON entry_comments (faction_id) WHERE faction_id IS NOT NULL;
CREATE INDEX ix_entry_comments_place ON entry_comments (place_id) WHERE place_id IS NOT NULL;
CREATE INDEX ix_entry_comments_parent ON entry_comments (parent_id);
CREATE INDEX ix_entry_comments_campaign ON entry_comments (campaign_id);

-- Recherche globale : rowid = identifiant * 8 + 6 (voir migration 008).
CREATE TRIGGER trg_search_entry_comment_insert AFTER INSERT ON entry_comments BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 6, 'entry_comment', NEW.id, NEW.campaign_id, NULL, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_entry_comment_update AFTER UPDATE OF body ON entry_comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 6;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 6, 'entry_comment', NEW.id, NEW.campaign_id, NULL, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_entry_comment_delete AFTER DELETE ON entry_comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 6;
END;
