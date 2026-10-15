-- Ordre libre des PNJ et des factions dans l'index des personnages (0 partout : ordre alphabétique
-- tant que le MJ n'a rien rangé).
ALTER TABLE npcs ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE factions ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;

-- Types de lieux personnalisables par campagne (libellé, icône, ordre des menus).
-- places.kind contient la clé (key) d'un type de la même campagne ; « lieu » est le type par défaut.
CREATE TABLE place_kinds (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT 'pin',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, key)
);

INSERT INTO place_kinds (campaign_id, key, label, icon, sort_order)
SELECT c.id, d.key, d.label, d.icon, d.sort_order
  FROM campaigns c
 CROSS JOIN (
        SELECT 'region' AS key, 'Région' AS label, 'region' AS icon, 0 AS sort_order
        UNION ALL SELECT 'ville', 'Ville', 'city', 1
        UNION ALL SELECT 'quartier', 'Quartier', 'district', 2
        UNION ALL SELECT 'batiment', 'Bâtiment', 'house', 3
        UNION ALL SELECT 'salle', 'Salle', 'door', 4
        UNION ALL SELECT 'donjon', 'Donjon, souterrain', 'cave', 5
        UNION ALL SELECT 'nature', 'Nature', 'tree', 6
        UNION ALL SELECT 'lieu', 'Lieu', 'pin', 7
       ) d;

-- Lieux d'un type inconnu (aucun en principe) : type par défaut.
UPDATE places SET kind = 'lieu'
 WHERE kind NOT IN (SELECT key FROM place_kinds k WHERE k.campaign_id = places.campaign_id);
