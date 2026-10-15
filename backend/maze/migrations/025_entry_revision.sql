-- Révision des fiches PNJ, factions et lieux (audit M1) : compteur qui augmente à chaque changement du texte de
-- la fiche (champs suivis par l'historique, entry_history.FIELDS), quel que soit le chemin (formulaire,
-- connecteur Claude, restauration d'une version, type de lieu supprimé, import). Le formulaire envoie la
-- révision sur laquelle il est parti (base_revision, facultatif) : 409 si elle a changé entre-temps.
-- Les positions, l'image de couverture et le rangement dans l'arbre ne changent pas la révision.

ALTER TABLE npcs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE factions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE places ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;

CREATE TRIGGER trg_npcs_revision AFTER UPDATE OF name, title, status, people, age, location, summary, appearance,
    personality, motivations, secrets, notes, tags ON npcs
WHEN OLD.name IS NOT NEW.name OR OLD.title IS NOT NEW.title OR OLD.status IS NOT NEW.status
  OR OLD.people IS NOT NEW.people OR OLD.age IS NOT NEW.age OR OLD.location IS NOT NEW.location
  OR OLD.summary IS NOT NEW.summary OR OLD.appearance IS NOT NEW.appearance
  OR OLD.personality IS NOT NEW.personality OR OLD.motivations IS NOT NEW.motivations
  OR OLD.secrets IS NOT NEW.secrets OR OLD.notes IS NOT NEW.notes OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE npcs SET revision = revision + 1 WHERE id = NEW.id;
END;

CREATE TRIGGER trg_factions_revision AFTER UPDATE OF name, kind, color, summary, description, goals, resources,
    secrets, notes, tags ON factions
WHEN OLD.name IS NOT NEW.name OR OLD.kind IS NOT NEW.kind OR OLD.color IS NOT NEW.color
  OR OLD.summary IS NOT NEW.summary OR OLD.description IS NOT NEW.description OR OLD.goals IS NOT NEW.goals
  OR OLD.resources IS NOT NEW.resources OR OLD.secrets IS NOT NEW.secrets OR OLD.notes IS NOT NEW.notes
  OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE factions SET revision = revision + 1 WHERE id = NEW.id;
END;

CREATE TRIGGER trg_places_revision AFTER UPDATE OF name, kind, summary, ambiance, description, secrets, notes, tags
    ON places
WHEN OLD.name IS NOT NEW.name OR OLD.kind IS NOT NEW.kind OR OLD.summary IS NOT NEW.summary
  OR OLD.ambiance IS NOT NEW.ambiance OR OLD.description IS NOT NEW.description OR OLD.secrets IS NOT NEW.secrets
  OR OLD.notes IS NOT NEW.notes OR OLD.tags IS NOT NEW.tags
BEGIN
    UPDATE places SET revision = revision + 1 WHERE id = NEW.id;
END;
