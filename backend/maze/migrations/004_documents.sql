-- Fiches rédigées : fiche de campagne (racine), scénarios et sous-pages.

CREATE TABLE documents (
    id          INTEGER PRIMARY KEY,
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
);
CREATE INDEX ix_documents_campaign ON documents (campaign_id);
CREATE INDEX ix_documents_parent ON documents (parent_id);
-- Une seule fiche de campagne par campagne.
CREATE UNIQUE INDEX ux_documents_root ON documents (campaign_id) WHERE kind = 'campaign';

-- Mentions @PNJ / @faction extraites du contenu (liens croisés « Apparaît dans »).
CREATE TABLE document_mentions (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL,
    target_id   INTEGER NOT NULL,
    PRIMARY KEY (document_id, target_kind, target_id)
);
CREATE INDEX ix_document_mentions_target ON document_mentions (target_kind, target_id);

-- Images insérées dans les fiches.
CREATE TABLE document_images (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    file        TEXT NOT NULL,
    thumb       TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_document_images_document ON document_images (document_id);

CREATE TRIGGER trg_npcs_delete_mentions AFTER DELETE ON npcs BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'npc' AND target_id = OLD.id;
END;

CREATE TRIGGER trg_factions_delete_mentions AFTER DELETE ON factions BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'faction' AND target_id = OLD.id;
END;
