-- Accès de Foundry VTT (module maze-sync) : jetons en lecture seule, voir foundry.py.

-- Jetons créés dans Administration > Foundry. Seule l'empreinte (SHA-256) est conservée : le jeton
-- n'est affiché qu'à sa création. Supprimer la ligne révoque le jeton.
CREATE TABLE foundry_tokens (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    -- Quatre premiers caractères aléatoires du jeton, pour le reconnaître dans la liste.
    hint         TEXT NOT NULL DEFAULT '',
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used_at TEXT
);

-- Campagnes lisibles avec un jeton. Sans ligne : le jeton ne voit rien.
CREATE TABLE foundry_grants (
    token_id    INTEGER NOT NULL REFERENCES foundry_tokens(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    PRIMARY KEY (token_id, campaign_id)
);
CREATE INDEX ix_foundry_grants_campaign ON foundry_grants (campaign_id);
