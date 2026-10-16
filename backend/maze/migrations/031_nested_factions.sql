-- Factions imbriquées : une appartenance relie à une faction (faction_id) soit un PNJ (npc_id), soit une autre
-- faction (member_faction_id), qui en devient une sous-faction. Une faction peut être membre de plusieurs factions.
-- Règles tenues par la base : exactement un des deux membres renseigné, pas d'appartenance à soi-même, chef
-- (is_leader) réservé aux PNJ, une seule appartenance par paire (membre, faction). Les règles qui demandent de
-- parcourir les autres lignes (même campagne, pas de cycle A dans B dans A) sont vérifiées par l'API, l'import et
-- le connecteur (api.membership_problem).
-- La campagne d'une appartenance est désormais celle de sa faction (faction_id, toujours renseignée), et non plus
-- celle du PNJ.
--
-- SQLite ne sait pas rendre une colonne facultative : table reconstruite (clés étrangères suspendues par
-- db.migrate, vérifiées avant de valider), identifiants conservés. Aucun déclencheur ni aucune vue ne cite
-- memberships. Index : ix_memberships_faction recréé ; les contraintes UNIQUE (npc_id, faction_id) et
-- (member_faction_id, faction_id) ont chacune leur index implicite, qui sert aussi aux suppressions en cascade
-- d'un PNJ ou d'une faction membre (colonne en tête).
CREATE TABLE memberships__nested (
    id                INTEGER PRIMARY KEY,
    npc_id            INTEGER REFERENCES npcs(id) ON DELETE CASCADE,
    faction_id        INTEGER NOT NULL REFERENCES factions(id) ON DELETE CASCADE,
    role              TEXT NOT NULL DEFAULT '',
    is_leader         INTEGER NOT NULL DEFAULT 0,
    member_faction_id INTEGER REFERENCES factions(id) ON DELETE CASCADE,
    CHECK ((npc_id IS NULL) <> (member_faction_id IS NULL)),
    CHECK (member_faction_id IS NULL OR (member_faction_id <> faction_id AND is_leader = 0)),
    UNIQUE (npc_id, faction_id),
    UNIQUE (member_faction_id, faction_id)
);
INSERT INTO memberships__nested (id, npc_id, faction_id, role, is_leader)
SELECT id, npc_id, faction_id, role, is_leader FROM memberships;
DROP TABLE memberships;
ALTER TABLE memberships__nested RENAME TO memberships;
CREATE INDEX ix_memberships_faction ON memberships (faction_id);
