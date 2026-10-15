-- Journal du connecteur Claude : nom de la campagne au moment de l'action (re-revue de l'audit). Le journal est
-- repris de la base en service à une restauration, alors que les campagnes viennent de l'archive : un numéro
-- de campagne peut alors désigner une autre campagne. Les entrées existantes reçoivent le nom actuel ; une
-- entrée sans nom est affichée avec le nom actuel de la campagne (connector._state).

ALTER TABLE mcp_log ADD COLUMN campaign_name TEXT NOT NULL DEFAULT '';

UPDATE mcp_log SET campaign_name = COALESCE((SELECT name FROM campaigns WHERE id = mcp_log.campaign_id), '')
 WHERE campaign_id IS NOT NULL;
