-- Recherche globale : index plein texte (FTS5) des PNJ, factions, fiches rédigées et commentaires.
-- Accents et majuscules ignorés (unicode61 remove_diacritics). L'index est tenu à jour par des
-- déclencheurs ; celui des fiches rédigées utilise la fonction html_text() que Maze déclare sur
-- chacune de ses connexions (db.connect) : modifier le titre ou le contenu d'une fiche depuis un
-- autre outil SQLite échouerait donc.
-- rowid = identifiant * 8 + type (1 PNJ, 2 faction, 3 fiche, 4 commentaire) : mise à jour directe.

CREATE VIRTUAL TABLE search_index USING fts5(
    kind UNINDEXED, ref_id UNINDEXED, campaign_id UNINDEXED, doc_id UNINDEXED,
    title, body,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- PNJ
INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
SELECT id * 8 + 1, 'npc', id, campaign_id, NULL, name,
       summary || ' ' || appearance || ' ' || personality || ' ' || motivations || ' ' || secrets || ' ' || notes
       || ' ' || title || ' ' || people || ' ' || location || ' ' || replace(replace(replace(tags, '[', ''), ']', ''), '"', '')
  FROM npcs;

CREATE TRIGGER trg_search_npc_insert AFTER INSERT ON npcs BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.appearance || ' ' || NEW.personality || ' ' || NEW.motivations || ' ' || NEW.secrets
            || ' ' || NEW.notes || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_npc_update AFTER UPDATE OF name, title, people, location, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.appearance || ' ' || NEW.personality || ' ' || NEW.motivations || ' ' || NEW.secrets
            || ' ' || NEW.notes || ' ' || NEW.title || ' ' || NEW.people || ' ' || NEW.location
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_npc_delete AFTER DELETE ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
END;

-- Factions
INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
SELECT id * 8 + 2, 'faction', id, campaign_id, NULL, name,
       summary || ' ' || description || ' ' || goals || ' ' || resources || ' ' || secrets || ' ' || notes
       || ' ' || kind || ' ' || replace(replace(replace(tags, '[', ''), ']', ''), '"', '')
  FROM factions;

CREATE TRIGGER trg_search_faction_insert AFTER INSERT ON factions BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.description || ' ' || NEW.goals || ' ' || NEW.resources || ' ' || NEW.secrets
            || ' ' || NEW.notes || ' ' || NEW.kind || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_faction_update AFTER UPDATE OF name, kind, summary, description, goals, resources,
    secrets, notes, tags ON factions BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 2;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 2, 'faction', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || NEW.description || ' ' || NEW.goals || ' ' || NEW.resources || ' ' || NEW.secrets
            || ' ' || NEW.notes || ' ' || NEW.kind || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_faction_delete AFTER DELETE ON factions BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 2;
END;

-- Fiches rédigées (fiche de campagne, scénarios, sous-pages)
INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
SELECT id * 8 + 3, 'document', id, campaign_id, id, title, html_text(content) FROM documents;

CREATE TRIGGER trg_search_document_insert AFTER INSERT ON documents BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 3, 'document', NEW.id, NEW.campaign_id, NEW.id, NEW.title, html_text(NEW.content));
END;
CREATE TRIGGER trg_search_document_update AFTER UPDATE OF title, content ON documents BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 3;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 3, 'document', NEW.id, NEW.campaign_id, NEW.id, NEW.title, html_text(NEW.content));
END;
CREATE TRIGGER trg_search_document_delete AFTER DELETE ON documents BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 3;
END;

-- Commentaires
INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
SELECT c.id * 8 + 4, 'comment', c.id, d.campaign_id, c.document_id, c.author_name, c.body
  FROM comments c JOIN documents d ON d.id = c.document_id;

CREATE TRIGGER trg_search_comment_insert AFTER INSERT ON comments BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 4, 'comment', NEW.id, (SELECT campaign_id FROM documents WHERE id = NEW.document_id),
            NEW.document_id, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_comment_update AFTER UPDATE OF body ON comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 4;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 4, 'comment', NEW.id, (SELECT campaign_id FROM documents WHERE id = NEW.document_id),
            NEW.document_id, NEW.author_name, NEW.body);
END;
CREATE TRIGGER trg_search_comment_delete AFTER DELETE ON comments BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 4;
END;
