-- Chronologie (module facultatif) : une liste ordonnée d'événements par campagne, affichée en liste ou en frise.
-- Le module est désactivé par défaut (campaigns.timeline_enabled) : tant qu'il l'est, l'interface n'en montre rien.
-- La date est un texte libre (« 0 742 999.M41 », « vers 850.M39 », « Séance 4 ») : aucun calendrier, l'ordre est
-- manuel (sort_order). La description est le HTML de l'éditeur (encadrés, @mentions) ; les fiches qu'elle cite
-- sont recopiées dans timeline_mentions par le serveur, comme document_mentions pour les fiches rédigées.
ALTER TABLE campaigns ADD COLUMN timeline_enabled INTEGER NOT NULL DEFAULT 0 CHECK (timeline_enabled IN (0, 1));

CREATE TABLE timeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    date_label  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    revision    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX ix_timeline_events_campaign ON timeline_events (campaign_id, sort_order, id);

-- Révision (base_revision, 409 en cas de conflit), comme les PNJ, factions et lieux (migration 025).
CREATE TRIGGER trg_timeline_events_revision AFTER UPDATE OF date_label, title, description, tags ON timeline_events
BEGIN
    UPDATE timeline_events SET revision = OLD.revision + 1 WHERE id = NEW.id;
END;

-- Fiches citées par la description d'un événement (npc, faction, place, doc), limitées à sa campagne.
CREATE TABLE timeline_mentions (
    event_id    INTEGER NOT NULL REFERENCES timeline_events(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('npc', 'faction', 'place', 'doc')),
    target_id   INTEGER NOT NULL,
    PRIMARY KEY (event_id, target_kind, target_id)
);
CREATE INDEX ix_timeline_mentions_target ON timeline_mentions (target_kind, target_id);

-- Mentions retirées quand la fiche citée disparaît (identifiants jamais réutilisés depuis la migration 026, mais
-- la section « Chronologie » d'une fiche ne doit pas compter de lignes orphelines).
CREATE TRIGGER trg_timeline_mentions_npc AFTER DELETE ON npcs BEGIN
    DELETE FROM timeline_mentions WHERE target_kind = 'npc' AND target_id = OLD.id;
END;
CREATE TRIGGER trg_timeline_mentions_faction AFTER DELETE ON factions BEGIN
    DELETE FROM timeline_mentions WHERE target_kind = 'faction' AND target_id = OLD.id;
END;
CREATE TRIGGER trg_timeline_mentions_place AFTER DELETE ON places BEGIN
    DELETE FROM timeline_mentions WHERE target_kind = 'place' AND target_id = OLD.id;
END;
CREATE TRIGGER trg_timeline_mentions_doc AFTER DELETE ON documents BEGIN
    DELETE FROM timeline_mentions WHERE target_kind = 'doc' AND target_id = OLD.id;
END;

-- Recherche globale : rowid = identifiant * 8 + 7 (voir migration 008), type « event ».
CREATE TRIGGER trg_search_event_insert AFTER INSERT ON timeline_events BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 7, 'event', NEW.id, NEW.campaign_id, NULL, NEW.title,
            NEW.date_label || ' ' || html_text(NEW.description)
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_event_update AFTER UPDATE OF date_label, title, description, tags ON timeline_events BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 7;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 7, 'event', NEW.id, NEW.campaign_id, NULL, NEW.title,
            NEW.date_label || ' ' || html_text(NEW.description)
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_event_delete AFTER DELETE ON timeline_events BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 7;
END;
