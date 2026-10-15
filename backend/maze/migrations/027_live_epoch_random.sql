-- Époque initiale aléatoire des fiches rédigées (audit DATA-8, contre-revue du lot 4) : toutes les fiches neuves
-- partaient de live_epoch = 0. Une fiche dont le numéro est repris (base restaurée d'une archive plus ancienne,
-- avant que le compteur ne soit reporté) avait la même époque que l'ancienne : un navigateur resté hors ligne sur
-- celle-ci y était accueilli et y versait ses modifications. Valeur sur 47 bits (nombre exact en JavaScript), jamais
-- 0 ; une époque donnée explicitement (restauration, import) est gardée.
CREATE TRIGGER trg_documents_live_epoch AFTER INSERT ON documents WHEN NEW.live_epoch = 0 BEGIN
    UPDATE documents SET live_epoch = (random() & 140737488355327) + 1 WHERE id = NEW.id;
END;
