-- Lieux épinglés par chaque MJ (accueil de l'onglet Lieux). Préférence personnelle : ni exportée avec la
-- campagne ni partagée entre comptes ; disparaît avec le lieu ou le compte.
CREATE TABLE place_pins (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    place_id   INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (user_id, place_id)
);
CREATE INDEX ix_place_pins_place ON place_pins (place_id);
