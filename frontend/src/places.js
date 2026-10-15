// Arborescence des lieux : outils communs à l'index, à l'arbre, au fil d'Ariane et aux cartes.

import { normalize, storage } from './util.js';

export const byOrder = (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'fr');

export const childrenOf = (places, parentId) => places.filter((p) => (p.parent_id ?? null) === parentId).sort(byOrder);

/** Sous-lieux de chaque lieu, triés (clé null : lieux de premier niveau). */
export function childrenMap(places) {
  const map = new Map();
  for (const p of places) {
    const key = p.parent_id ?? null;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(p);
  }
  for (const list of map.values()) list.sort(byOrder);
  return map;
}

/** Lieu et tous ses sous-lieux. */
export function subtreeIds(places, rootId) {
  const ids = new Set([rootId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of places) {
      if (p.parent_id != null && ids.has(p.parent_id) && !ids.has(p.id)) {
        ids.add(p.id);
        grew = true;
      }
    }
  }
  return ids;
}

/** Lieu et ses ancêtres, du lieu vers la racine. */
export function pathIds(places, id) {
  const byId = places instanceof Map ? places : new Map(places.map((p) => [p.id, p]));
  const out = [];
  for (let p = byId.get(id); p && out.length < 1000; p = byId.get(p.parent_id)) out.push(p.id);
  return out;
}

/** Ancêtres d'un lieu (de la racine vers son parent), pour afficher un chemin. */
export function ancestorsOf(byId, place) {
  const out = [];
  for (let p = byId.get(place.parent_id); p && out.length < 30; p = byId.get(p.parent_id)) out.unshift(p);
  return out;
}

// --- Regroupement par type -----------------------------------------------------------------------

/** À partir de combien de sous-lieux une liste est regroupée par type. */
export const GROUP_MIN = 12;
export const OTHER_KINDS = '*';

/** Clé d'un groupe de sous-lieux : parent (0 pour le premier niveau) et type ('*' : autres types). */
export const groupKey = (parentId, kind) => `g:${parentId ?? 0}:${kind}`;
export const isGroupKey = (key) => typeof key === 'string' && key.startsWith('g:');
export const groupParent = (key) => { const n = Number(key.split(':')[1]); return n || null; };

/**
 * Sous-lieux regroupés par type, dans l'ordre des types de la campagne : un groupe par type d'au moins deux lieux,
 * les types isolés réunis dans « Autres types ». Null quand le regroupement n'apporte rien (liste courte,
 * un seul groupe).
 */
export function groupsOf(parentId, kids, kinds) {
  if (kids.length < GROUP_MIN) return null;
  const order = new Map((kinds || []).map((k, i) => [k.key, i]));
  const byKind = new Map();
  for (const p of kids) {
    if (!byKind.has(p.kind)) byKind.set(p.kind, []);
    byKind.get(p.kind).push(p);
  }
  const groups = [];
  const others = [];
  const sorted = [...byKind.entries()].sort((a, b) => (order.get(a[0]) ?? 999) - (order.get(b[0]) ?? 999));
  for (const [kind, items] of sorted) {
    if (items.length >= 2) groups.push({ key: groupKey(parentId, kind), parentId, kind, items });
    else others.push(...items);
  }
  if (others.length) groups.push({ key: groupKey(parentId, OTHER_KINDS), parentId, kind: OTHER_KINDS, items: others });
  return groups.length >= 2 ? groups : null;
}

// --- Recherche dans l'arbre ----------------------------------------------------------------------

/** Lieux dont le nom ou une étiquette contient le texte (sans accents ni casse) et, s'il est donné, du type. */
export function matchPlaces(places, query, kind) {
  const q = normalize(query.trim());
  return places.filter((p) => (!kind || p.kind === kind)
    && (!q || normalize(p.name).includes(q) || (p.tags || []).some((t) => normalize(t).includes(q))));
}

/** Découpe un nom autour du texte cherché : [avant, trouvé, après], ou null (rien à surligner). */
export function splitMatch(name, query) {
  const q = normalize(query.trim());
  if (!q) return null;
  const n = normalize(name);
  const i = n.indexOf(q);
  if (i < 0 || n.length !== name.length) return null;
  return [name.slice(0, i), name.slice(i, i + q.length), name.slice(i + q.length)];
}

// --- Lieux récemment consultés (navigateur) -------------------------------------------------------

const RECENT_MAX = 8;
const recentKey = (cid) => `maze:places:recent:${cid}`;

export function recentPlaces(cid) {
  try {
    const ids = JSON.parse(storage.get(recentKey(cid)) || '[]');
    return Array.isArray(ids) ? ids.filter(Number.isInteger) : [];
  } catch {
    return [];
  }
}

export function rememberPlace(cid, id) {
  const ids = [id, ...recentPlaces(cid).filter((x) => x !== id)].slice(0, RECENT_MAX);
  storage.set(recentKey(cid), JSON.stringify(ids));
}
