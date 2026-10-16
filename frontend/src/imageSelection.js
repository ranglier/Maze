/*
 * Administration > Images : logique pure de la sélection multiple, du tri et de la suppression groupée (sans React ni
 * DOM), testée par test/imageSelection.test.mjs. Une image est désignée par sa clé `${source}-${id}` (source :
 * library, gallery, document, map), stable quand on change de filtre, de recherche ou de tri.
 */
import { compareText } from './i18n/core.js';

/** Images supprimées par appel de POST /api/admin/campaigns/<id>/images/delete (limite du serveur). */
export const BATCH_SIZE = 200;

export const imageKey = (img) => `${img.source}-${img.id}`;

/** Nouvelle sélection avec `key` ajoutée ou retirée (l'ancienne n'est pas modifiée). */
export function toggleKey(selection, key) {
  const next = new Set(selection);
  if (next.has(key)) next.delete(key); else next.add(key);
  return next;
}

/** Clés de `anchor` à `key` comprises, dans l'ordre affiché `order`. Ancre absente (ou masquée) : `key` seule. */
export function rangeKeys(order, anchor, key) {
  const end = order.indexOf(key);
  if (end < 0) return [];
  const start = anchor == null ? -1 : order.indexOf(anchor);
  if (start < 0) return [key];
  return order.slice(Math.min(start, end), Math.max(start, end) + 1);
}

/** Sélection augmentée de la plage `anchor` > `key` (Maj+clic). */
export function addRange(selection, order, anchor, key) {
  return new Set([...selection, ...rangeKeys(order, anchor, key)]);
}

/** Sélection augmentée de toutes les clés affichées ; celles masquées par le filtre restent. */
export function selectAll(selection, keys) {
  return new Set([...selection, ...keys]);
}

/** Retire les clés qui ne correspondent plus à aucune image (après rechargement). Même objet si rien ne change. */
export function pruneSelection(selection, images) {
  if (!images) return selection.size ? new Set() : selection;
  const known = new Set(images.map(imageKey));
  if ([...selection].every((k) => known.has(k))) return selection;
  return new Set([...selection].filter((k) => known.has(k)));
}

/** Nombre d'images sélectionnées qui ne sont pas affichées (filtre, recherche). */
export function hiddenCount(selection, shownKeys) {
  const shown = shownKeys instanceof Set ? shownKeys : new Set(shownKeys);
  let n = 0;
  for (const k of selection) if (!shown.has(k)) n += 1;
  return n;
}

// --- Tri ------------------------------------------------------------------------------------------------------------

/** Tris proposés ; libellés traduits à l'affichage par ImagesSection. */
export const SORTS = [{ key: 'recent' }, { key: 'oldest' }, { key: 'heaviest' }, { key: 'name' }];
export const DEFAULT_SORT = 'recent';
export const normalizeSort = (value) => (SORTS.some((s) => s.key === value) ? value : DEFAULT_SORT);

const byDate = (a, b) => (a.created_at || '').localeCompare(b.created_at || '') || a.id - b.id;   // dates ISO (UTC)

/** Nouvelle liste triée. `titleOf(img)` : titre affiché (tri « Nom »). */
export function sortImages(images, sort, titleOf) {
  const list = [...images];
  switch (normalizeSort(sort)) {
    case 'oldest': return list.sort(byDate);
    case 'heaviest': return list.sort((a, b) => (b.bytes || 0) - (a.bytes || 0) || byDate(b, a));
    case 'name':
      return list.sort((a, b) => compareText(titleOf(a), titleOf(b), { sensitivity: 'base', numeric: true })
        || byDate(b, a));
    default: return list.sort((a, b) => byDate(b, a));
  }
}

// --- Suppression groupée ----------------------------------------------------------------------------------------

/**
 * Récapitulatif d'une suppression : nombre et taille, nombre par sorte, conséquences.
 * covers : portraits, emblèmes ou illustrations de fiches (remplacés par l'image suivante de leur galerie) ;
 * shownInDocs / documents : illustrations encore affichées dans le texte, et fiches rédigées concernées ;
 * maps : cartes interactives supprimées avec leurs épingles.
 */
export function deletionSummary(images) {
  const bySource = { library: 0, gallery: 0, document: 0, map: 0 };
  let bytes = 0;
  let covers = 0;
  let shownInDocs = 0;
  const docs = new Set();
  for (const img of images) {
    bySource[img.source] = (bySource[img.source] || 0) + 1;
    bytes += img.bytes || 0;
    if (img.source === 'gallery' && img.cover) covers += 1;
    if (img.source === 'document' && img.used) {
      shownInDocs += 1;
      if (img.document) docs.add(img.document.id);
    }
  }
  return { count: images.length, bytes, bySource, covers, shownInDocs, documents: docs.size, maps: bySource.map };
}

/** Éléments de la requête : [{source, id}]. */
export const toItems = (images) => images.map((img) => ({ source: img.source, id: img.id }));

/** Lots de `size` éléments au plus. */
export function chunks(list, size = BATCH_SIZE) {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

/**
 * Réponses des lots réunies : { deleted, editing: [clés], missing }. « editing » : illustration laissée car une fiche
 * rédigée qui l'affiche est en cours d'écriture (elle reste sélectionnée) ; « missing » : déjà supprimée, ignorée.
 */
export function mergeResults(results) {
  let deleted = 0;
  let missing = 0;
  const editing = [];
  for (const res of results) {
    deleted += res?.deleted || 0;
    for (const s of res?.skipped || []) {
      if (s.reason === 'editing') editing.push(imageKey(s));
      else missing += 1;
    }
  }
  return { deleted, editing, missing };
}
