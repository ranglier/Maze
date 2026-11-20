-- Dépôt d'image demandé par Claude (uploads.py, outil request_upload de mcp_upload.py) : Claude ne peut pas transmettre
-- à Maze une image jointe à la conversation ; il crée un ticket et donne au MJ un lien vers une page de Maze
-- (#/depot/<jeton>) où celui-ci dépose l'image, qui arrive directement dans la cible. Claude interroge ensuite le
-- ticket pour connaître l'image créée.
--
-- Jeton conservé haché (SHA-256, comme les jetons du connecteur) ; lien valable 10 minutes, à usage unique.
-- target : library (bibliothèque de la campagne), npc, faction, place (galerie de la fiche) ou document (illustration
-- d'une fiche rédigée) ; target_id vide pour la bibliothèque, sans clé étrangère (fiche de l'une des quatre tables :
-- supprimée entre-temps, le dépôt est refusé). image_id : ligne créée par le dépôt, dans library_images, images ou
-- document_images selon la cible (sans clé étrangère : l'image peut être supprimée ensuite).
-- Tickets périmés : passés à expired, puis supprimés un jour après leur échéance, à la création d'un ticket.
-- Données passagères : ni export de campagne, ni import.

CREATE TABLE upload_tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    target      TEXT NOT NULL CHECK (target IN ('library', 'npc', 'faction', 'place', 'document')),
    target_id   INTEGER,
    caption     TEXT NOT NULL DEFAULT '',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at  INTEGER NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'used', 'expired')),
    image_id    INTEGER,
    used_by     INTEGER,
    used_at     TEXT,
    CHECK ((target = 'library') = (target_id IS NULL))
);
CREATE INDEX ix_upload_tickets_campaign ON upload_tickets (campaign_id);
CREATE INDEX ix_upload_tickets_expires ON upload_tickets (expires_at);
CREATE INDEX ix_upload_tickets_created_by ON upload_tickets (created_by);
