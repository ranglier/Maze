-- Natures de lien personnalisables par campagne (libellé, couleur, sens par défaut).
-- relations.type contient la clé (key) d'une nature de la même campagne.

CREATE TABLE relation_types (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#8a93a3',
    directed    INTEGER NOT NULL DEFAULT 0,
    hint        TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, key)
);

-- Natures par défaut pour les campagnes existantes.
INSERT INTO relation_types (campaign_id, key, label, color, directed, hint, sort_order)
SELECT c.id, d.key, d.label, d.color, d.directed, d.hint, d.sort_order
  FROM campaigns c
  CROSS JOIN (
        SELECT 'allie' AS key, 'Allié' AS label, '#5fb49c' AS color, 0 AS directed, '' AS hint, 0 AS sort_order
        UNION ALL SELECT 'ennemi', 'Ennemi', '#d2555a', 0, '', 1
        UNION ALL SELECT 'famille', 'Famille', '#e0a33b', 0, '', 2
        UNION ALL SELECT 'amour', 'Amour', '#d77ba6', 0, '', 3
        UNION ALL SELECT 'rival', 'Rival', '#e07b39', 0, '', 4
        UNION ALL SELECT 'mentor', 'Mentor', '#6fa8dc', 1, 'la source est mentor de la cible', 5
        UNION ALL SELECT 'hierarchie', 'Hiérarchie', '#9c88d8', 1, 'la source commande la cible', 6
        UNION ALL SELECT 'affaires', 'Affaires', '#b8b25a', 0, '', 7
        UNION ALL SELECT 'dette', 'Dette', '#a8825e', 1, 'la source doit quelque chose à la cible', 8
        UNION ALL SELECT 'autre', 'Autre', '#8a93a3', 0, '', 9
  ) d;
