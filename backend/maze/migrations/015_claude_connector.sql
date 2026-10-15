-- Connecteur Claude (serveur MCP sous /mcp, autorisation OAuth 2.1). Voir connector.py.

-- Réglages de l'instance : une seule ligne. client_id vide = identifiants pas encore générés.
-- Le secret n'est conservé que sous forme d'empreinte (SHA-256) : il n'est affiché qu'à sa création.
CREATE TABLE mcp_settings (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER NOT NULL DEFAULT 0,
    client_id   TEXT NOT NULL DEFAULT '',
    secret_hash TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO mcp_settings (id) VALUES (1);

-- Campagnes ouvertes à Claude : lecture seule, ou lecture et écriture. Sans ligne : aucun accès.
CREATE TABLE mcp_grants (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL CHECK (mode IN ('read', 'write'))
);

-- Jetons délivrés à Claude (empreintes SHA-256). Un jeton n'est valable que pour un administrateur
-- actif dont la clé de session n'a pas changé (mot de passe modifié, compte désactivé : déconnecté).
CREATE TABLE mcp_tokens (
    id                 INTEGER PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_key        TEXT NOT NULL,
    access_hash        TEXT NOT NULL UNIQUE,
    refresh_hash       TEXT NOT NULL UNIQUE,
    access_expires_at  INTEGER NOT NULL,
    refresh_expires_at INTEGER NOT NULL,
    -- Date de l'autorisation (conservée quand Claude renouvelle ses jetons).
    authorized_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used_at       TEXT
);
CREATE INDEX ix_mcp_tokens_user ON mcp_tokens (user_id);

-- Journal des actions de Claude (les plus récentes sont conservées).
CREATE TABLE mcp_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_id     INTEGER,
    tool        TEXT NOT NULL,
    campaign_id INTEGER,
    target      TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 1,
    detail      TEXT NOT NULL DEFAULT ''
);
