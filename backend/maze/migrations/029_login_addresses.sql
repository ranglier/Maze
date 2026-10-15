-- Adresses qui se sont connectées à un compte par mot de passe (connexion à Maze, autorisation du connecteur Claude
-- par un administrateur), pour la limitation des essais (throttle.py, audit M13, vérification finale R1) : une adresse
-- connue a sa propre file d'essais pour ce compte, qu'un flot d'essais venu d'ailleurs ne retarde ni ne refuse. Gardée
-- en base pour survivre aux redémarrages ; 30 jours après la dernière connexion réussie, 32 adresses par compte au
-- plus (les plus anciennes retirées), purge à chaque connexion réussie et au démarrage. Rien n'y est écrit par les
-- jetons (connecteur Claude, Foundry). Données de sécurité de l'instance : jamais dans l'export d'une campagne ; à la
-- restauration d'une sauvegarde, celles de la base en service sont reprises (backup._keep_current_access).
-- IF NOT EXISTS : rejouable sur une base déjà migrée (tests qui ramènent la version du schéma en arrière).
CREATE TABLE IF NOT EXISTS login_addresses (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address TEXT NOT NULL,
    last_login_at TEXT NOT NULL,
    PRIMARY KEY (user_id, address)
);
CREATE INDEX IF NOT EXISTS ix_login_addresses_last ON login_addresses(last_login_at);
