-- Jetons Foundry liés à un administrateur actif (audit M12) : un jeton dont le créateur a été supprimé
-- (created_by NULL), rétrogradé ou désactivé continuait d'exporter les campagnes, secrets compris.
-- Désormais admin.update_user et delete_user suppriment ces jetons et foundry.token_campaigns les refuse ;
-- ceux qui existent déjà sont purgés ici. Les clés étrangères étant suspendues pendant une migration
-- (db.migrate), les droits de ces jetons sont supprimés explicitement.

DELETE FROM foundry_grants WHERE token_id IN (
    SELECT id FROM foundry_tokens
     WHERE created_by IS NULL
        OR created_by NOT IN (SELECT id FROM users WHERE role = 'admin' AND active = 1));

DELETE FROM foundry_tokens
 WHERE created_by IS NULL
    OR created_by NOT IN (SELECT id FROM users WHERE role = 'admin' AND active = 1);
