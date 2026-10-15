-- Index de recherche des PNJ sans les anciens champs « peuple » et « lieu » (audit M23 = QUAL-3) : retirés de
-- l'interface le 5 octobre (commit 278f537), ils restaient indexés, et la recherche trouvait une fiche par un mot
-- que la fiche ne montre pas. Colonnes et valeurs gardées (export, import, historique) ; seuls les déclencheurs
-- de la recherche et les lignes des PNJ dans l'index changent. Champs indexés : registre backend/maze/entries.py
-- (search=True), vérifié par tests.test_registry.

DROP TRIGGER trg_search_npc_insert;
DROP TRIGGER trg_search_npc_update;

CREATE TRIGGER trg_search_npc_insert AFTER INSERT ON npcs BEGIN
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;
CREATE TRIGGER trg_search_npc_update AFTER UPDATE OF name, title, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs BEGIN
    DELETE FROM search_index WHERE rowid = OLD.id * 8 + 1;
    INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
    VALUES (NEW.id * 8 + 1, 'npc', NEW.id, NEW.campaign_id, NULL, NEW.name,
            NEW.summary || ' ' || html_text(NEW.appearance) || ' ' || html_text(NEW.personality) || ' ' || html_text(NEW.motivations) || ' ' || html_text(NEW.secrets)
            || ' ' || html_text(NEW.notes) || ' ' || NEW.title
            || ' ' || replace(replace(replace(NEW.tags, '[', ''), ']', ''), '"', ''));
END;

-- PNJ existants : réindexés avec les mêmes champs.
DELETE FROM search_index WHERE rowid IN (SELECT id * 8 + 1 FROM npcs);
INSERT INTO search_index (rowid, kind, ref_id, campaign_id, doc_id, title, body)
SELECT id * 8 + 1, 'npc', id, campaign_id, NULL, name,
       summary || ' ' || html_text(appearance) || ' ' || html_text(personality) || ' ' || html_text(motivations) || ' ' || html_text(secrets)
       || ' ' || html_text(notes) || ' ' || title
       || ' ' || replace(replace(replace(tags, '[', ''), ']', ''), '"', '')
  FROM npcs;
