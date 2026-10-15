-- Bibliothèque d'images d'une campagne (Administration > Images) : images déposées sans être encore
-- rattachées à une fiche. Les utiliser sur une fiche en fait une copie : la bibliothèque reste intacte.
CREATE TABLE library_images (
    id            INTEGER PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    file          TEXT NOT NULL,
    thumb         TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    caption       TEXT NOT NULL DEFAULT '',
    width         INTEGER,
    height        INTEGER,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_library_images_campaign ON library_images (campaign_id);
