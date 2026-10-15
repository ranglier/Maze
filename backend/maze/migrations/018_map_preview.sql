-- Cartes interactives : fond gardé en pleine définition (jusqu'à 8192 px) et version d'affichage réduite
-- à 4096 px (preview), chargée d'abord ; vide si le fond n'est pas plus grand (ou copié d'une image de Maze).
ALTER TABLE maps ADD COLUMN preview TEXT NOT NULL DEFAULT '';
