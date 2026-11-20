-- Corbeille (maze/trash.py) : un PNJ, une faction, un lieu, une fiche rédigée (avec ses sous-pages), un événement de
-- chronologie ou une carte interactive supprimé y reste 30 jours, d'où il se restaure tel qu'il était, avec ce qui en
-- dépendait. Une ligne par suppression : l'objet et toutes les lignes parties avec lui (cascades, nettoyage
-- polymorphe des déclencheurs, effets du code), en JSON (payload), et ses fichiers, déplacés sous
-- media/<campagne>/.trash/ (files : chemins d'origine). counts : effectifs pour le résumé affiché (« 3 sous-pages »).
-- deleted_by : compte qui a supprimé (ou qui a autorisé Claude), sans clé étrangère : la ligne reste si le compte
-- disparaît. via : interface ou connecteur Claude. Suppression d'une campagne : sa corbeille part avec elle (cascade).
CREATE TABLE trash (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('npc', 'faction', 'place', 'document', 'event', 'map')),
    object_id   INTEGER NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    deleted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    deleted_by  INTEGER,
    via         TEXT NOT NULL DEFAULT 'interface' CHECK (via IN ('interface', 'claude')),
    payload     TEXT NOT NULL DEFAULT '{}',
    files       TEXT NOT NULL DEFAULT '[]',
    counts      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_trash_campaign ON trash (campaign_id, deleted_at);
