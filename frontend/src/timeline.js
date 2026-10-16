/*
 * Chronologie (module facultatif d'une campagne) : logique pure, sans React ni DOM, testée par
 * test/timeline.test.mjs. L'ordre des événements est manuel (sort_order côté serveur) : l'interface calcule la
 * nouvelle liste complète des identifiants (PUT /api/campaigns/<id>/events/order) ou le before_id d'une insertion.
 */

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
