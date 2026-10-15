-- Comptes utilisateurs et attribution des campagnes.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'mj' CHECK (role IN ('admin', 'mj')),
    active        INTEGER NOT NULL DEFAULT 1,
    -- Incrémenté à chaque changement de mot de passe : invalide les sessions ouvertes.
    session_epoch INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_login_at TEXT
);

CREATE TABLE campaign_members (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'mj' CHECK (role IN ('mj')),
    PRIMARY KEY (campaign_id, user_id)
);
CREATE INDEX ix_campaign_members_user ON campaign_members (user_id);
