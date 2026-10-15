// Cartes interactives : lieux épinglés et cartes qui se contiennent (calculs purs, sans React).
import { childrenOf } from '../../places.js';

export { childrenOf };
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Le lieu puis ses ancêtres, du plus proche au plus lointain. */
export function ancestors(byId, id) {
  const out = [];
  for (let p = byId.get(id); p && out.length < 100; p = byId.get(p.parent_id)) out.push(p.id);
  return out;
}

/** Chemin d'anneaux qui montre le lieu : [lieu épinglé, …, lieu] (null s'il n'est pas sur la carte). */
export function focusPath(pins, byId, placeId) {
  const anc = ancestors(byId, placeId);
  const pinned = new Set(pins.map((p) => p.place_id));
  const i = anc.findIndex((id) => pinned.has(id));
  return i < 0 ? null : anc.slice(0, i + 1).reverse();
}

/** Carte où trouver le lieu au plus près (épinglé lui-même, sinon l'ancêtre le plus proche). */
export function bestMapFor(maps, byId, placeId) {
  const anc = ancestors(byId, placeId);
  let best = null;
  let rank = Infinity;
  for (const m of maps) {
    for (const pid of m.pins) {
      const r = anc.indexOf(pid);
      if (r >= 0 && r < rank) { best = m; rank = r; }
    }
  }
  return best;
}

/** Cartes plus larges menant à celle-ci (fil d'Ariane), de la plus large à elle-même. */
export function mapTrail(maps, byId, map) {
  const trail = [map];
  const seen = new Set([map.id]);
  let cur = map;
  while (cur.place_id != null && trail.length < 12) {
    const anc = ancestors(byId, cur.place_id);
    let parent = null;
    let rank = Infinity;
    for (const m of maps) {
      if (seen.has(m.id)) continue;
      for (const pid of m.pins) {
        const r = anc.indexOf(pid);
        if (r >= 0 && r < rank) { parent = m; rank = r; }
      }
    }
    if (!parent) break;
    trail.unshift(parent);
    seen.add(parent.id);
    cur = parent;
  }
  return trail;
}
