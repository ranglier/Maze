-- Cartes interactives (Lieux > Carte interactive) : une image de fond importée (gardée jusqu'à 4096 px)
-- sur laquelle les lieux de la campagne sont épinglés librement. Une carte peut représenter un lieu
-- (place_id : la carte de Scintilla) : depuis une carte plus large, on « entre » dans celle du lieu.
CREATE TABLE maps (
    id            INTEGER PRIMARY KEY,
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
);
CREATE INDEX ix_maps_campaign ON maps (campaign_id);
CREATE INDEX ix_maps_place ON maps (place_id);

-- Épingle d'un lieu sur une carte : position en proportion de l'image (0 à 1), indépendante de sa
-- résolution (remplacer le fond par une meilleure version ne décale rien).
CREATE TABLE map_pins (
    id       INTEGER PRIMARY KEY,
    map_id   INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    x        REAL NOT NULL CHECK (x >= 0 AND x <= 1),
    y        REAL NOT NULL CHECK (y >= 0 AND y <= 1),
    UNIQUE (map_id, place_id)
);
CREATE INDEX ix_map_pins_place ON map_pins (place_id);
