-- Clé de session aléatoire par compte. Une session (cookie) n'est valable que si sa clé est celle
-- du compte : un identifiant de compte réutilisé (compte supprimé puis recréé) ou une base restaurée
-- ne réactivent jamais une ancienne session. La clé change avec session_epoch (mot de passe
-- modifié, compte désactivé).

ALTER TABLE users ADD COLUMN session_key TEXT NOT NULL DEFAULT '';
UPDATE users SET session_key = lower(hex(randomblob(16)));

CREATE TRIGGER trg_users_new_session_key AFTER INSERT ON users WHEN NEW.session_key = '' BEGIN
    UPDATE users SET session_key = lower(hex(randomblob(16))) WHERE id = NEW.id;
END;

CREATE TRIGGER trg_users_rotate_session_key AFTER UPDATE OF session_epoch ON users BEGIN
    UPDATE users SET session_key = lower(hex(randomblob(16))) WHERE id = NEW.id;
END;
