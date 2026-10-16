// Arborescence des factions de l'index des personnages (onglet Personnages, rangement par faction).
// Les factions forment un graphe orienté sans cycle (une faction peut être membre de plusieurs factions, migration
// 031) : chaque faction apparaît sous CHACUN de ses parents, et chaque occurrence a sa clé de chemin (« 74/77 »),
// qui porte l'état déplié. Les cycles sont interdits par la base ; on garde une protection (jamais redescendre dans
// un ancêtre, et une faction inaccessible depuis les racines devient racine).
import { compareText } from './i18n/core.js';

const byName = (a, b) => compareText(a.name, b.name, { sensitivity: 'base' });
/** Ordre choisi par le MJ (glisser-déposer), sinon alphabétique (sort_order à 0 tant que rien n'est rangé). */
export const byOrder = (a, b) => (a.sort_order || 0) - (b.sort_order || 0) || byName(a, b);
/** PNJ d'une faction : ordre du MJ, puis chefs en tête, puis nom. */
const memberOrder = (a, b) => (a.npc.sort_order || 0) - (b.npc.sort_order || 0)
  || Number(Boolean(b.membership.is_leader)) - Number(Boolean(a.membership.is_leader)) || byName(a.npc, b.npc);

const all = () => true;

/**
 * Structure des factions d'un graphe (sans filtre) : parents, sous-factions et PNJ de chaque faction (triés),
 * racines (factions membres d'aucune faction, puis, protection contre les cycles, les factions qu'aucune racine
 * n'atteint), PNJ membres d'au moins une faction.
 */
export function factionStructure(graph) {
  const factions = new Map(graph.factions.map((f) => [f.id, f]));
  const npcs = new Map(graph.npcs.map((n) => [n.id, n]));
  const parents = new Map();
  const subs = new Map();
  const members = new Map();
  for (const f of graph.factions) { parents.set(f.id, []); subs.set(f.id, []); members.set(f.id, []); }
  const inFaction = new Set();
  for (const m of graph.memberships) {
    const parent = factions.get(m.faction_id);
    if (!parent) continue;
    if (m.member_faction_id != null) {
      const sub = factions.get(m.member_faction_id);
      if (!sub || sub === parent) continue;
      parents.get(sub.id).push(parent);
      subs.get(parent.id).push(sub);
    } else if (npcs.has(m.npc_id)) {
      members.get(parent.id).push({ npc: npcs.get(m.npc_id), membership: m });
      inFaction.add(m.npc_id);
    }
  }
  for (const list of parents.values()) list.sort(byOrder);
  for (const list of subs.values()) list.sort(byOrder);
  for (const list of members.values()) list.sort(memberOrder);

  const sorted = graph.factions.slice().sort(byOrder);
  const roots = sorted.filter((f) => parents.get(f.id).length === 0);
  const reached = new Set();
  const reach = (start) => {
    const stack = [start];
    while (stack.length) {
      const id = stack.pop();
      if (reached.has(id)) continue;
      reached.add(id);
      for (const c of subs.get(id)) stack.push(c.id);
    }
  };
  for (const f of roots) reach(f.id);
  for (const f of sorted) if (!reached.has(f.id)) { roots.push(f); reach(f.id); }
  return { factions, parents, subs, members, roots, inFaction };
}

/**
 * Forêt des factions, filtrée. Options :
 *   - factionMatch(f) : la faction correspond à la recherche (défaut : toutes) ;
 *   - npcVisible(n) : le PNJ correspond (recherche et statut ; défaut : tous) ;
 *   - memberVisible(n) : PNJ montré sous une faction qui correspond (défaut : npcVisible) ;
 *   - showNpcs : PNJ dans l'arbre (faux pour le filtre « Factions ») ;
 *   - structure : factionStructure(graph) déjà calculée.
 * Garde les factions qui correspondent, celles qui ont un PNJ visible et les ancêtres des unes et des autres.
 * Renvoie les racines : { faction, path, depth, matched, children, npcs: [{ npc, membership }], otherParents }.
 */
export function factionForest(graph, {
  factionMatch = all, npcVisible = all, memberVisible = npcVisible, showNpcs = true, structure = null,
} = {}) {
  const { subs, members, parents, roots } = structure || factionStructure(graph);
  const keep = new Map();
  const visiting = new Set();
  const kept = (f) => {
    if (keep.has(f.id)) return keep.get(f.id);
    if (visiting.has(f.id)) return false;   // cycle
    visiting.add(f.id);
    const ok = factionMatch(f) || (showNpcs && members.get(f.id).some((x) => npcVisible(x.npc)))
      || subs.get(f.id).some(kept);
    visiting.delete(f.id);
    keep.set(f.id, ok);
    return ok;
  };
  const ancestors = new Set();
  const build = (f, parent, parentPath, depth) => {
    const path = parentPath ? `${parentPath}/${f.id}` : String(f.id);
    const matched = Boolean(factionMatch(f));
    const shown = matched ? memberVisible : npcVisible;
    const npcs = showNpcs ? members.get(f.id).filter((x) => shown(x.npc)) : [];
    ancestors.add(f.id);
    const children = subs.get(f.id).filter((c) => !ancestors.has(c.id) && kept(c))
      .map((c) => build(c, f, path, depth + 1));
    ancestors.delete(f.id);
    const otherParents = parents.get(f.id).filter((p) => p !== parent);
    return { faction: f, path, depth, matched, children, npcs, otherParents };
  };
  return roots.filter(kept).map((f) => build(f, null, '', 0));
}

/** Chemins (clés) de chaque occurrence de chaque faction dans l'arbre complet : Map id > [« 1/4/9 », …]. */
export function factionPaths(graph, structure = null) {
  const { subs, roots } = structure || factionStructure(graph);
  const paths = new Map();
  const ancestors = new Set();
  const walk = (f, prefix) => {
    const path = prefix ? `${prefix}/${f.id}` : String(f.id);
    if (!paths.has(f.id)) paths.set(f.id, []);
    paths.get(f.id).push(path);
    ancestors.add(f.id);
    for (const c of subs.get(f.id)) if (!ancestors.has(c.id)) walk(c, path);
    ancestors.delete(f.id);
  };
  for (const f of roots) walk(f, '');
  return paths;
}

/**
 * Clés à déplier pour montrer une fiche : pour un PNJ (`self` vrai, ids = ses factions), tous les chemins vers ses
 * factions, factions comprises ; pour une faction (`self` faux, ids = [elle]), tous ses chemins d'ancêtres.
 */
export function branchKeys(paths, ids, { self = false } = {}) {
  const keys = new Set();
  for (const id of ids) {
    for (const path of paths.get(id) || []) {
      const parts = path.split('/');
      const end = self ? parts.length : parts.length - 1;
      for (let i = 1; i <= end; i += 1) keys.add(parts.slice(0, i).join('/'));
    }
  }
  return keys;
}
