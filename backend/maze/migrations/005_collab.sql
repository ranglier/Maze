-- Édition à plusieurs en temps réel : journal des modifications Yjs de chaque fiche.
-- Le serveur ne fusionne rien lui-même : il conserve et rediffuse les modifications,
-- les navigateurs les fusionnent (CRDT). Le journal est compacté régulièrement.

CREATE TABLE document_updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- numéro d'ordre, jamais réutilisé
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    data        BLOB NOT NULL,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_document_updates_doc ON document_updates (document_id, id);

-- Dernière modification prise en compte dans documents.content (HTML dérivé, pour les
-- rétroliens « Apparaît dans », la recherche et les sauvegardes lisibles).
ALTER TABLE documents ADD COLUMN content_seq INTEGER NOT NULL DEFAULT 0;

-- Incrémenté quand le contenu est remplacé par l'API (journal vidé) : un navigateur resté hors ligne
-- pendant ce temps ne doit pas réinjecter son ancien document.
ALTER TABLE documents ADD COLUMN live_epoch INTEGER NOT NULL DEFAULT 0;
