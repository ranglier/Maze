// factionTree.js : arborescence des factions de l'index des personnages (racines, plusieurs parents, cycles,
// filtre qui garde les ancêtres, PNJ dans plusieurs factions, tri).
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow } from './helpers.mjs';

fakeWindow();
const { branchKeys, factionForest, factionPaths, factionStructure } = await import('../src/factionTree.js');

const faction = (id, name, sort_order = 0) => ({ id, name, sort_order });
const npc = (id, name, extra = {}) => ({ id, name, sort_order: 0, status: 'vivant', ...extra });
const sub = (member_faction_id, faction_id) => ({ npc_id: null, member_faction_id, faction_id });
const member = (npc_id, faction_id, extra = {}) => ({ npc_id, member_faction_id: null, faction_id, ...extra });

// Les Sept Couronnes (1) > Maison Lannister (2) > Maison Clegane (3) > Bannerets du Chien (4)
// Dorne (5) ; Maison Martell (6) membre des Sept Couronnes et de Dorne ; Compagnie libre (7), seule.
const graph = {
  factions: [faction(1, 'Les Sept Couronnes'), faction(2, 'Maison Lannister'), faction(3, 'Maison Clegane'),
    faction(4, 'Bannerets du Chien'), faction(5, 'Dorne'), faction(6, 'Maison Martell'), faction(7, 'Compagnie libre')],
  npcs: [npc(1, 'Tywin'), npc(2, 'Cersei'), npc(3, 'Sandor', { status: 'mort' }), npc(4, 'Doran'),
    npc(5, 'Oberyn'), npc(6, 'Bronn')],
  memberships: [sub(2, 1), sub(3, 2), sub(4, 3), sub(6, 1), sub(6, 5),
    member(2, 2), member(1, 2, { is_leader: true }), member(3, 3), member(4, 6, { is_leader: true }), member(5, 6),
    member(5, 5)],
};
const names = (nodes) => nodes.map((n) => n.faction.name);
const find = (nodes, path) => {
  for (const n of nodes) {
    if (n.path === path) return n;
    const hit = find(n.children, path);
    if (hit) return hit;
  }
  return null;
};

test('racines : factions membres d’aucune faction, triées ; sous-factions à leur place', () => {
  const forest = factionForest(graph);
  assert.deepEqual(names(forest), ['Compagnie libre', 'Dorne', 'Les Sept Couronnes']);
  const crowns = forest[2];
  assert.equal(crowns.path, '1');
  assert.deepEqual(names(crowns.children), ['Maison Lannister', 'Maison Martell']);
  const clegane = find(forest, '1/2/3');
  assert.equal(clegane.depth, 2);
  assert.deepEqual(names(clegane.children), ['Bannerets du Chien']);
  assert.equal(clegane.children[0].path, '1/2/3/4');
  assert.deepEqual(factionStructure(graph).roots.map((f) => f.id), [7, 5, 1]);
});

test('plusieurs parents : la faction apparaît sous chacun, avec les autres parents', () => {
  const forest = factionForest(graph);
  const underCrowns = find(forest, '1/6');
  const underDorne = find(forest, '5/6');
  assert.ok(underCrowns && underDorne);
  assert.deepEqual(underCrowns.otherParents.map((f) => f.name), ['Dorne']);
  assert.deepEqual(underDorne.otherParents.map((f) => f.name), ['Les Sept Couronnes']);
  assert.deepEqual(find(forest, '1/2').otherParents, []);
  assert.deepEqual(factionPaths(graph).get(6), ['5/6', '1/6']);
  assert.deepEqual(factionPaths(graph).get(4), ['1/2/3/4']);
});

test('cycle artificiel : ni boucle infinie ni redescente dans un ancêtre', () => {
  const cyclic = { ...graph, memberships: [...graph.memberships, sub(1, 4)] };   // Sept Couronnes membre des Bannerets
  // Plus aucune racine pour ce groupe : la première faction non atteinte (ordre alphabétique) devient racine.
  const forest = factionForest(cyclic);
  assert.deepEqual(names(forest), ['Compagnie libre', 'Dorne', 'Bannerets du Chien']);
  const top = forest[2];
  assert.deepEqual(names(top.children), ['Les Sept Couronnes']);
  const clegane = find(forest, '4/1/2/3');
  assert.deepEqual(clegane.children, [], 'Bannerets du Chien, ancêtre, pas repris');
  // Cycle à deux sans racine du tout.
  const pair = { factions: [faction(1, 'A'), faction(2, 'B')], npcs: [], memberships: [sub(1, 2), sub(2, 1)] };
  const f2 = factionForest(pair);
  assert.deepEqual(names(f2), ['A']);
  assert.deepEqual(names(f2[0].children), ['B']);
  assert.deepEqual(f2[0].children[0].children, []);
  assert.deepEqual([...factionPaths(pair).entries()], [[1, ['1']], [2, ['1/2']]]);
  // Filtre sur un cycle : terminé, et garde l'ancêtre.
  assert.deepEqual(names(factionForest(pair, { factionMatch: (f) => f.name === 'B' })), ['A']);
});

test('filtre : nœuds qui correspondent et leurs ancêtres, rien d’autre', () => {
  const q = (text) => (x) => x.name.toLowerCase().includes(text);
  // Faction profonde : ses ancêtres seulement.
  let forest = factionForest(graph, { factionMatch: q('banneret'), npcVisible: q('banneret') });
  assert.deepEqual(names(forest), ['Les Sept Couronnes']);
  assert.deepEqual(names(forest[0].children), ['Maison Lannister']);
  assert.deepEqual(forest[0].children[0].npcs, [], 'PNJ de la Maison Lannister, qui ne correspondent pas : absents');
  assert.deepEqual(names(find(forest, '1/2/3').children), ['Bannerets du Chien']);
  // PNJ : ses factions (tous les chemins) et leurs ancêtres.
  forest = factionForest(graph, { factionMatch: () => false, npcVisible: q('oberyn') });
  assert.deepEqual(names(forest), ['Dorne', 'Les Sept Couronnes']);
  assert.deepEqual(forest[0].npcs.map((x) => x.npc.name), ['Oberyn']);
  assert.deepEqual(find(forest, '5/6').npcs.map((x) => x.npc.name), ['Oberyn']);
  assert.deepEqual(names(forest[1].children), ['Maison Martell']);
  // Faction qui correspond : ses PNJ visibles (filtre de statut), pas ses sous-factions.
  forest = factionForest(graph, { factionMatch: q('clegane'), npcVisible: () => false, memberVisible: (n) => n.status === 'mort' });
  const clegane = find(forest, '1/2/3');
  assert.ok(clegane.matched);
  assert.deepEqual(clegane.npcs.map((x) => x.npc.name), ['Sandor']);
  assert.deepEqual(clegane.children, []);
  assert.equal(find(forest, '1/2').matched, false);
  // Sans PNJ (filtre « Factions ») : arbre des factions seul.
  forest = factionForest(graph, { showNpcs: false });
  assert.ok(forest.every((n) => n.npcs.length === 0));
  assert.deepEqual(find(forest, '1/2/3').npcs, []);
  forest = factionForest(graph, { showNpcs: false, npcVisible: q('oberyn'), factionMatch: () => false });
  assert.deepEqual(forest, [], 'sans PNJ, un PNJ qui correspond ne garde pas ses factions');
});

test('PNJ dans plusieurs factions : présent dans chacune ; « sans faction » pour les autres', () => {
  const forest = factionForest(graph);
  assert.deepEqual(find(forest, '5').npcs.map((x) => x.npc.name), ['Oberyn']);
  assert.deepEqual(find(forest, '1/6').npcs.map((x) => x.npc.name), ['Doran', 'Oberyn']);
  assert.deepEqual(find(forest, '5/6').npcs.map((x) => x.npc.name), ['Doran', 'Oberyn']);
  assert.deepEqual([...factionStructure(graph).inFaction].sort(), [1, 2, 3, 4, 5]);   // Bronn sans faction
});

test('tri : ordre du MJ puis nom ; chef en tête parmi les PNJ de même ordre', () => {
  let forest = factionForest(graph);
  assert.deepEqual(find(forest, '1/2').npcs.map((x) => x.npc.name), ['Tywin', 'Cersei'], 'chef en tête');
  const ordered = {
    ...graph,
    factions: graph.factions.map((f) => (f.id === 6 ? { ...f, sort_order: 1 } : f.id === 2 ? { ...f, sort_order: 2 } : f)),
    npcs: graph.npcs.map((n) => (n.id === 2 ? { ...n, sort_order: -1 } : n)),
  };
  forest = factionForest(ordered);
  assert.deepEqual(names(find(forest, '1').children), ['Maison Martell', 'Maison Lannister']);
  assert.deepEqual(find(forest, '1/2').npcs.map((x) => x.npc.name), ['Cersei', 'Tywin'], 'ordre du MJ avant le chef');
  const accents = { factions: [faction(1, 'Été'), faction(2, 'Eau'), faction(3, 'Zinc')], npcs: [], memberships: [] };
  assert.deepEqual(names(factionForest(accents)), ['Eau', 'Été', 'Zinc']);
});

test('branches à déplier pour une fiche ouverte', () => {
  const paths = factionPaths(graph);
  // PNJ Oberyn (Maison Martell et Dorne) : tous les chemins, factions comprises.
  assert.deepEqual([...branchKeys(paths, [6, 5], { self: true })].sort(), ['1', '1/6', '5', '5/6']);
  // Faction profonde : ses ancêtres, pas elle-même.
  assert.deepEqual([...branchKeys(paths, [4])].sort(), ['1', '1/2', '1/2/3']);
  assert.deepEqual([...branchKeys(paths, [6])].sort(), ['1', '5']);
  assert.deepEqual([...branchKeys(paths, [1])], []);
  assert.deepEqual([...branchKeys(paths, [99])], []);
});
