-- Liens entre fiches rédigées : mentions « doc:<id> » (ex. « voir la page La filature de Rathbone »).
-- La table document_mentions les accueille déjà ; il faut seulement les retirer quand la fiche visée disparaît
-- (la suppression d'une fiche emporte ses sous-pages par cascade, ce déclencheur s'applique à chacune).
CREATE TRIGGER trg_documents_delete_links AFTER DELETE ON documents BEGIN
    DELETE FROM document_mentions WHERE target_kind = 'doc' AND target_id = OLD.id;
END;
