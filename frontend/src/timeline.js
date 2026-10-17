/*
 * Chronologie (module facultatif d'une campagne) : logique pure, sans React ni DOM, testée par
 * test/timeline.test.mjs. L'ordre des événements est manuel (sort_order côté serveur) : l'interface calcule la
 * nouvelle liste complète des identifiants (PUT /api/campaigns/<id>/events/order) ou le before_id d'une insertion.
 */
import { t } from './i18n/core.js';

/** Identifiants dans l'ordre d'affichage. */
export const idsOf = (events) => (events || []).map((e) => e.id);

/** `ids` avec `id` déplacé avant `beforeId` (null : à la fin). Liste inchangée si l'un des deux est inconnu. */
export function moveBefore(ids, id, beforeId) {
  if (!ids.includes(id) || id === beforeId) return [...ids];
  if (beforeId != null && !ids.includes(beforeId)) return [...ids];
  const rest = ids.filter((x) => x !== id);
  const at = beforeId == null ? rest.length : rest.indexOf(beforeId);
  rest.splice(at, 0, id);
  return rest;
}

/** Glisser-déposer : `id` posé avant ou après `targetId` ('before' | 'after'). */
export function moveRelative(ids, id, targetId, where) {
  if (id === targetId) return [...ids];
  if (where === 'before') return moveBefore(ids, id, targetId);
  const rest = ids.filter((x) => x !== id);
  const i = rest.indexOf(targetId);
  if (i < 0 || !ids.includes(id)) return [...ids];
  return moveBefore(ids, id, rest[i + 1] ?? null);
}

/** `ids` avec `id` déplacé au rang `index` de la liste finale (borné). */
export function moveToIndex(ids, id, index) {
  if (!ids.includes(id)) return [...ids];
  const rest = ids.filter((x) => x !== id);
  const at = Math.max(0, Math.min(index, rest.length));
  rest.splice(at, 0, id);
  return rest;
}

/** before_id d'une insertion au rang `index` (0 : au début ; au-delà de la fin : null, ajout en fin de liste). */
export const beforeIdAt = (events, index) => (events?.[index] ? events[index].id : null);

/** Liste locale après la création d'un événement inséré avant `beforeId` (null : à la fin). */
export function insertBefore(events, created, beforeId) {
  const rest = events.filter((e) => e.id !== created.id);
  const at = beforeId == null ? -1 : rest.findIndex((e) => e.id === beforeId);
  if (at < 0) return [...rest, created];
  return [...rest.slice(0, at), created, ...rest.slice(at)];
}

/**
 * Places proposées par « Déplacer vers… » pour `id` : avant chacun des autres événements, puis à la fin.
 * [{ beforeId, current }] ; `current` : place actuelle de l'événement (avant son suivant, ou fin s'il est dernier).
 */
export function moveSlots(events, id) {
  const ids = idsOf(events);
  const i = ids.indexOf(id);
  if (i < 0) return [];
  const next = ids[i + 1] ?? null;
  const slots = ids.filter((x) => x !== id).map((beforeId) => ({ beforeId, current: beforeId === next }));
  slots.push({ beforeId: null, current: next === null });
  return slots;
}

/** Étiquettes de toutes les fiches, sans doublon (casse ignorée, première graphie gardée), triées. */
export function allTags(events, compare = (a, b) => a.localeCompare(b)) {
  const seen = new Map();
  for (const e of events || []) {
    for (const tag of e.tags || []) {
      const key = tag.toLowerCase();
      if (!seen.has(key)) seen.set(key, tag);
    }
  }
  return [...seen.values()].sort(compare);
}

/** Événements portant l'étiquette `tag` (casse ignorée) ; tous si `tag` est vide. */
export function filterByTag(events, tag) {
  if (!tag) return events || [];
  const key = tag.toLowerCase();
  return (events || []).filter((e) => (e.tags || []).some((x) => x.toLowerCase() === key));
}

/** Teinte stable (0 à 359) d'une étiquette, calculée depuis son texte (FNV-1a, casse ignorée). */
export function tagHue(tag) {
  let h = 0x811c9dc5;
  for (const ch of String(tag || '').toLowerCase()) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h % 360;
}

/** Couleur d'une étiquette, lisible sur le fond sombre de l'interface. */
export const tagColor = (tag) => `hsl(${tagHue(tag)} 52% 64%)`;

/** Fiches citées par un événement ([[kind, id]] du serveur), sans doublon, dans l'ordre. */
export function eventMentions(event) {
  const out = [];
  const seen = new Set();
  for (const m of event?.mentions || []) {
    const [kind, id] = Array.isArray(m) ? m : [m?.kind, m?.id];
    const key = `${kind}:${id}`;
    if (!kind || id == null || seen.has(key)) continue;
    seen.add(key);
    out.push({ kind, id: Number(id) });
  }
  return out;
}

/** Adresse de la fiche citée (PNJ, faction, lieu ou fiche rédigée). */
export function mentionHref(campaignId, kind, id) {
  if (kind === 'npc' || kind === 'faction') return `#/c/${campaignId}/personnages/${kind}-${id}`;
  if (kind === 'place') return `#/c/${campaignId}/lieux/${id}`;
  if (kind === 'doc') return `#/c/${campaignId}/scenarios/${id}`;
  return null;
}

/** Adresse d'un événement dans la chronologie. */
export const eventHref = (campaignId, id) => `#/c/${campaignId}/chronologie?event=${id}`;

/** Texte brut d'une description (HTML de l'éditeur), pour juger de sa longueur. */
export const plainText = (html) => String(html || '').replace(/<[^>]*>/g, ' ').replace(/&nbsp;|&#160;/g, ' ')
  .replace(/\s+/g, ' ').trim();

/** Description longue : repliée dans la liste (plus de 280 caractères, ou plus de 3 blocs). */
export function isLongDescription(html) {
  const blocks = (String(html || '').match(/<(p|h[1-6]|li|blockquote|tr|div)\b/g) || []).length;
  return plainText(html).length > 280 || blocks > 3;
}

/** Rang de la carte suivante au clavier dans la frise (flèches, Début, Fin) ; null pour une autre touche. */
export function nextIndex(index, key, count) {
  if (!count) return null;
  if (key === 'ArrowRight') return Math.min(index + 1, count - 1);
  if (key === 'ArrowLeft') return Math.max(index - 1, 0);
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}

export const MODES = ['list', 'frieze'];
/** Mode d'affichage gardé (« list » ou « frieze ») ; par défaut : liste sur téléphone en portrait, frise sinon. */
export function initialMode(stored, phonePortrait) {
  if (MODES.includes(stored)) return stored;
  return phonePortrait ? 'list' : 'frieze';
}

/** Pastilles d'étiquettes d'une ligne compacte : les `max` premières, et le nombre des autres (« +n »). */
export function tagDots(tags, max = 3) {
  const list = tags || [];
  return { shown: list.slice(0, max), more: Math.max(0, list.length - max) };
}

export const DENSITIES = ['normal', 'compact'];
/**
 * Clé de stockage de la densité, propre à chaque mode (liste, frise). La frise garde la clé d'avant la liste
 * compacte : le choix déjà fait est conservé.
 */
export const densityKey = (mode) => (mode === 'frieze' ? 'maze:timeline:density' : `maze:timeline:density:${MODES.includes(mode) ? mode : 'list'}`);
/** Densité gardée : « compact » ou, pour toute autre valeur (rien, valeur inconnue), « normal ». */
export const initialDensity = (stored) => (stored === 'compact' ? 'compact' : 'normal');

/*
 * Mini-carte de la frise : bande sous la frise, un trait par événement selon son rang, et un rectangle pour la
 * partie visible. `view` : { scrollLeft, clientWidth, scrollWidth } de la frise ; `layout` : { start, slot },
 * position (px) du premier emplacement dans la piste et largeur d'un emplacement.
 */
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const maxScroll = (view) => Math.max(0, (view.scrollWidth || 0) - (view.clientWidth || 0));

/** La frise déborde-t-elle de l'écran (mini-carte utile) ? */
export const overflows = (view) => maxScroll(view) > 1;

/** Rectangle de la partie visible, en fractions de la largeur totale : { left, width } (0 à 1). */
export function minimapWindow(view) {
  const total = view.scrollWidth || 0;
  if (total <= 0) return { left: 0, width: 1 };
  const width = clamp((view.clientWidth || 0) / total, 0, 1);
  return { left: clamp((view.scrollLeft || 0) / total, 0, 1 - width), width };
}

/** scrollLeft qui centre la frise sur la fraction `fraction` de sa largeur (clic dans la mini-carte), borné. */
export const scrollForFraction = (fraction, view) =>
  Math.round(clamp(fraction * (view.scrollWidth || 0) - (view.clientWidth || 0) / 2, 0, maxScroll(view)));

/** scrollLeft après un glissement du rectangle de `dx` px dans une mini-carte large de `mapWidth` px. */
export const scrollForDrag = (startLeft, dx, mapWidth, view) =>
  Math.round(clamp(startLeft + (mapWidth > 0 ? dx * (view.scrollWidth || 0) / mapWidth : 0), 0, maxScroll(view)));

/** Position du trait de l'événement de rang `index` (fraction de la largeur totale : milieu de son emplacement). */
export const markFraction = (index, layout, view) =>
  ((view.scrollWidth || 0) > 0 ? clamp((layout.start + (index + 0.5) * layout.slot) / view.scrollWidth, 0, 1) : 0);

/** Rangs (base 1) du premier et du dernier événement dont le milieu est visible : [first, last], ou null. */
export function eventsInView(view, count, layout) {
  if (!count || !(layout.slot > 0)) return null;
  const left = view.scrollLeft || 0;
  const right = left + (view.clientWidth || 0);
  const first = clamp(Math.ceil((left - layout.start) / layout.slot - 0.5), 0, count - 1);
  const last = clamp(Math.floor((right - layout.start) / layout.slot - 0.5), 0, count - 1);
  if (last < first) return [first + 1, first + 1];
  return [first + 1, last + 1];
}

/** Texte lu de la mini-carte (aria-valuetext) : « événements 12 à 24 sur 80 ». */
export function viewText(range, count) {
  if (!range) return '';
  const [first, last] = range;
  if (first === last) return t('événement {n} sur {count}', { n: first, count });
  return t('événements {first} à {last} sur {count}', { first, last, count });
}

/** Mini-carte au clavier : flèches = un écran, Début / Fin ; scrollLeft visé, ou null pour une autre touche. */
export function sliderScroll(key, view) {
  const left = view.scrollLeft || 0;
  const page = view.clientWidth || 0;
  if (key === 'ArrowRight' || key === 'ArrowDown' || key === 'PageDown') return Math.min(left + page, maxScroll(view));
  if (key === 'ArrowLeft' || key === 'ArrowUp' || key === 'PageUp') return Math.max(left - page, 0);
  if (key === 'Home') return 0;
  if (key === 'End') return maxScroll(view);
  return null;
}

/** Valeur de la mini-carte (aria-valuenow) : défilement en pourcentage, 0 à 100. */
export const scrollPercent = (view) => (maxScroll(view) > 0 ? Math.round((100 * (view.scrollLeft || 0)) / maxScroll(view)) : 0);
