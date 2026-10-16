-- Langue de l'interface choisie par chaque compte (menu du compte > Langue ; PUT /api/auth/locale). Vide par défaut :
-- « Automatique », c'est-à-dire la langue du navigateur si Maze la connaît, sinon MAZE_DEFAULT_LOCALE. Sert aussi aux
-- messages d'erreur du serveur (maze.i18n : compte connecté, puis en-tête Accept-Language, puis MAZE_DEFAULT_LOCALE).
ALTER TABLE users ADD COLUMN locale TEXT NOT NULL DEFAULT '';
