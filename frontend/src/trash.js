// Corbeille d'une campagne (logique pure, sans React) : genres d'éléments, filtres, délai avant l'effacement,
// adresse de l'élément restauré. Utilisé par components/TrashView.jsx, testé par test/trash.test.mjs.
import { N_, t } from './i18n/core.js';

// Genres d'éléments mis à la corbeille, dans l'ordre des filtres : icône (ui.jsx), libellé singulier (élément) et
// pluriel (filtre). Textes marqués par N_, traduits à l'affichage.
export const TRASH_KINDS = {
  npc: { icon: 'person', label: N_('PNJ'), filter: N_('PNJ') },
  faction: { icon: 'banner', label: N_('Faction'), filter: N_('Factions') },
  place: { icon: 'pin', label: N_('Lieu'), filter: N_('Lieux') },
  document: { icon: 'page', label: N_('Fiche rédigée'), filter: N_('Fiches rédigées') },
  event: { icon: 'timeline', label: N_('Événement'), filter: N_('Événements') },
  map: { icon: 'map', label: N_('Carte interactive'), filter: N_('Cartes interactives') },
};
export const KIND_ORDER = Object.keys(TRASH_KINDS);

/** Libellé traduit d'un genre (genre inconnu : la clé telle quelle). */
export const kindLabel = (kind) => (TRASH_KINDS[kind] ? t(TRASH_KINDS[kind].label) : kind);
export const kindIcon = (kind) => TRASH_KINDS[kind]?.icon || 'trash';

const DAY = 24 * 60 * 60 * 1000;

/** Date du serveur (ISO, « Z » final ; « AAAA-MM-JJ HH:MM:SS » sans fuseau lu en UTC). null si illisible. */
export function parseStamp(value) {
  if (!value) return null;
  let text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text)) text = `${text.replace(' ', 'T')}Z`;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Jours entiers restants avant l'effacement automatique (arrondi au jour supérieur, 0 au plus bas). null : inconnu. */
export function daysLeft(expiresAt, now = Date.now()) {
  const end = parseStamp(expiresAt);
  if (!end) return null;
  const left = end.getTime() - (now instanceof Date ? now.getTime() : now);
  return left <= 0 ? 0 : Math.ceil(left / DAY);
}

/** Effectifs par genre, dans l'ordre des filtres : [{ kind, count }] (genres absents écartés, inconnus à la fin). */
export function kindCounts(items) {
  const counts = new Map();
  for (const item of items || []) counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
  const known = KIND_ORDER.filter((k) => counts.has(k));
  const other = [...counts.keys()].filter((k) => !TRASH_KINDS[k]).sort();
  return [...known, ...other].map((kind) => ({ kind, count: counts.get(kind) }));
}

/** Éléments d'un genre ('' ou genre absent de la liste : tous). Ordre conservé (le serveur trie, plus récent d'abord). */
export function filterByKind(items, kind) {
  const list = items || [];
  if (!kind || !list.some((i) => i.kind === kind)) return list;
  return list.filter((i) => i.kind === kind);
}

/** Adresse de l'élément restauré dans l'interface (null si le genre n'a pas d'écran). */
export function objectHref(cid, kind, id) {
  if (!cid || id == null) return null;
  switch (kind) {
    case 'npc':
    case 'faction': return `#/c/${cid}/personnages/${kind}-${id}`;
    case 'place': return `#/c/${cid}/lieux/${id}`;
    case 'document': return `#/c/${cid}/scenarios/${id}`;
    case 'event': return `#/c/${cid}/chronologie?event=${id}`;
    case 'map': return `#/c/${cid}/cartes/${id}`;
    default: return null;
  }
}
