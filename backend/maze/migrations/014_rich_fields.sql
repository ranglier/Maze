-- Champs longs des PNJ, factions et lieux écrits avec l'éditeur des scénarios (HTML) : l'index de recherche
-- n'en garde que le texte (html_text, voir migration 008 ; sans effet sur les anciens textes en Markdown).

DROP TRIGGER trg_search_npc_insert;
CREATE TRIGGER trg_search_npc_insert AFTER INSERT ON npcs BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
DROP TRIGGER trg_search_npc_update;
CREATE TRIGGER trg_search_npc_update AFTER UPDATE OF name, title, people, location, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;

DROP TRIGGER trg_search_faction_insert;
CREATE TRIGGER trg_search_faction_insert AFTER INSERT ON factions BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.goals) || ' ' || html_text(NEW.resources)
            || ' ' || html_text(NEW.secrets) || ' ' || html_text(NEW.notes) || ' ' || NEW.kind
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
DROP TRIGGER trg_search_faction_update;
CREATE TRIGGER trg_search_faction_update AFTER UPDATE OF name, kind, summary, description, goals, resources,
    secrets, notes, tags ON factions BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 2;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.goals) || ' ' || html_text(NEW.resources)
            || ' ' || html_text(NEW.secrets) || ' ' || html_text(NEW.notes) || ' ' || NEW.kind
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;

DROP TRIGGER trg_search_place_insert;
CREATE TRIGGER trg_search_place_insert AFTER INSERT ON places BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.ambiance) || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
DROP TRIGGER trg_search_place_update;
CREATE TRIGGER trg_search_place_update AFTER UPDATE OF name, summary, ambiance, description, secrets, notes, tags ON places BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 5;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 5, 'place', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.ambiance) || ' ' || html_text(NEW.description) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
