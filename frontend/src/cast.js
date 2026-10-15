// Distribution d'un scénario (audit QUAL-12) : fiches citées (@) dans le scénario et toutes ses pages, plus les
// ajouts manuels, moins les retraits. Même règle côté serveur : api.scenario_maps_of (fiche > « Sur les cartes ») et
// foundry.scenarios_of (export Foundry) ; les trois sont comparées sur des campagnes aléatoires communes
// (backend/tests/test_cast_rules.py écrit et vérifie frontend/test/fixtures/cast-cases.json, cast.test.mjs le rejoue
// ici). Calculée dans le navigateur : la carte d'un scénario suit aussitôt un ajout, un retrait ou une mention.
import { nodeId } from './util.js';

export function childrenOf(docs, parentId) {
  return docs.filter((d) => d.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
}

/** Fiches citées dans une fiche et toutes ses sous-pages (identifiants de nœuds de la carte). */
export function mentionedNodes(data, docId) {
  const nodes = new Set();
  if (!data) return nodes;
  const visit = (id) => {
    for (const [kind, eid] of data.mentions[id] || []) {
      if (kind === 'npc' || kind === 'faction') nodes.add(nodeId(kind, eid));
    }
    for (const child of childrenOf(data.documents, id)) visit(child.id);
  };
  visit(docId);
  return nodes;
}

/**
 * Distribution de la carte d'un scénario.
 * `rows` : réglages chargés pour ce scénario ({kind, id, state}), sinon ceux de `data.overrides`.
 * Renvoie { nodes, mentioned, excluded } (ensembles d'identifiants de nœuds).
 */
export function castOf(data, docId, rows = null) {
  const mentioned = mentionedNodes(data, docId);
  const nodes = new Set(mentioned);
  const excluded = new Set();
  const settings = rows
    ? rows.filter((r) => r.state !== 'auto').map((r) => [r.kind, r.id, r.state])
    : (data?.overrides?.[docId] || []);
  for (const [kind, eid, state] of settings) {
    const id = nodeId(kind, eid);
    if (state === 'added') nodes.add(id);
    if (state === 'excluded') { nodes.delete(id); if (mentioned.has(id)) excluded.add(id); }
  }
  return { nodes, mentioned, excluded };
}

/** Voisins directs (liens et appartenances) des fiches affichées. */
export function neighborsOf(graph, nodes) {
  const around = new Set();
  const add = (a, b) => {
    if (nodes.has(a) && !nodes.has(b)) around.add(b);
    if (nodes.has(b) && !nodes.has(a)) around.add(a);
  };
  for (const r of graph.relations) add(nodeId(r.source_kind, r.source_id), nodeId(r.target_kind, r.target_id));
  for (const m of graph.memberships) add(nodeId('npc', m.npc_id), nodeId('faction', m.faction_id));
  return around;
}

export const scenariosOf = (data) => (data ? childrenOf(data.documents, data.root_id).filter((d) => d.kind === 'scenario') : []);

