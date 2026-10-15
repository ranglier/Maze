-- Historique des versions des fiches rédigées, et modèles de fiches.

-- Versions : instantanés du contenu (HTML compressé zlib). Automatiques (avant une séance
-- d'écriture, en fin de séance, avant un remplacement), manuelles (nommées) ou de restauration.
CREATE TABLE document_versions (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('auto', 'manual', 'restore')),
    label       TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    content     BLOB NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,       -- taille du HTML décompressé
    digest      TEXT NOT NULL DEFAULT '',         -- empreinte du HTML : évite les doublons
    author_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_document_versions_document ON document_versions (document_id, id);

-- Modèles de fiches, communs à tous les MJ de l'instance.
CREATE TABLE templates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
