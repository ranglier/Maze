/* global __MAZE_SCHEMA_VERSION__ */
// Version du schéma de l'éditeur (audit H3), injectée par esbuild (build.mjs, define) depuis schema-version.json.
// Annoncée au serveur (paramètre v du WebSocket, en-tête X-Maze-Version), qui refuse une autre version : un onglet
// resté sur une ancienne interface supprimerait sinon chez tous les MJ les blocs que son schéma ne connaît pas.
export const SCHEMA_VERSION = typeof __MAZE_SCHEMA_VERSION__ === 'undefined' ? 0 : __MAZE_SCHEMA_VERSION__;

// Événement diffusé quand le serveur refuse cette version (detail : { version } du serveur).
export const STALE_EVENT = 'maze:stale-client';
