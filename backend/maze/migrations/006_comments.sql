-- Commentaires sur les fiches rédigées (comme dans un document partagé).
-- Un fil = un commentaire racine (parent_id NULL), ancré dans le texte par une marque
-- <span data-comment="id"> ; les réponses ont parent_id = id du fil.

CREATE TABLE comments (
    id          INTEGER PRIMARY KEY,
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
CREATE INDEX ix_comments_document ON comments (document_id);
CREATE INDEX ix_comments_parent ON comments (parent_id);
