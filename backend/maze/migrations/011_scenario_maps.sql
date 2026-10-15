-- Carte des relations propre à chaque scénario.
-- Une ligne par fiche (PNJ ou faction) qui a une position ou un réglage sur la carte du scénario :
--   state 'auto'      présente si elle est citée (@) dans le scénario ou ses pages
--   state 'added'     ajoutée à la main, même sans citation
--   state 'excluded'  retirée à la main, même citée
-- Les liens restent ceux de la campagne : seule la sélection et la disposition sont propres au scénario.

CREATE TABLE scenario_nodes (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('npc', 'faction')),
    entity_id   INTEGER NOT NULL,
    state       TEXT NOT NULL DEFAULT 'auto' CHECK (state IN ('auto', 'added', 'excluded')),
    pos_x       REAL,
    pos_y       REAL,
    PRIMARY KEY (document_id, entity_kind, entity_id)
);
CREATE INDEX ix_scenario_nodes_entity ON scenario_nodes (entity_kind, entity_id);

-- Nettoyage à la suppression d'une fiche (références polymorphes, sans clé étrangère).
CREATE TRIGGER trg_npcs_delete_scenario AFTER DELETE ON npcs BEGIN
    DELETE FROM scenario_nodes WHERE entity_kind = 'npc' AND entity_id = OLD.id;
END;

CREATE TRIGGER trg_factions_delete_scenario AFTER DELETE ON factions BEGIN
    DELETE FROM scenario_nodes WHERE entity_kind = 'faction' AND entity_id = OLD.id;
END;
