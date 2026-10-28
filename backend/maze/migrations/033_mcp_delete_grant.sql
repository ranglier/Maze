-- Connecteur Claude : troisième niveau d'accès par campagne, « delete » (lecture, écriture et suppression), qui
-- ouvre en plus les outils de suppression (delete_entry, delete_document, delete_event, delete_map, delete_comment).
-- « write » garde tout ce qu'il permettait, y compris les outils destructeurs d'avant (delete_relation…).
--
-- SQLite ne sait pas modifier une contrainte CHECK : table reconstruite (clés étrangères suspendues par db.migrate,
-- vérifiées avant de valider), lignes recopiées telles quelles. Même clé et même ON DELETE CASCADE qu'en 015 ;
-- aucun index, déclencheur ni vue ne cite mcp_grants.

CREATE TABLE mcp_grants__delete (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL CHECK (mode IN ('read', 'write', 'delete'))
);
INSERT INTO mcp_grants__delete (campaign_id, mode) SELECT campaign_id, mode FROM mcp_grants;
DROP TABLE mcp_grants;
ALTER TABLE mcp_grants__delete RENAME TO mcp_grants;
