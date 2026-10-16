// graph/elements.js : éléments de la carte des relations (Cytoscape). Appartenances d'un PNJ ou d'une sous-faction
// (member_faction_id, migration 031) : arête vers la faction, filtre « appartenances », factions masquées, distribution
// d'un scénario.
import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_FILTERS, buildElements, npcRingColors } from '../src/graph/elements.js';

const graph = {
  npcs: [{ id: 1, name: 'Hart', status: 'vivant' }],
  factions: [{ id: 7, name: 'Conseil', color: '#7c8796' }, { id: 8, name: 'Garde', color: '#4f7fb5' }],
  memberships: [
    { id: 1, npc_id: 1, member_faction_id: null, faction_id: 8, role: 'Capitaine', is_leader: true },
    { id: 2, npc_id: null, member_faction_id: 8, faction_id: 7, role: 'Bras armé', is_leader: false },
  ],
  relations: [],
  relation_types: [],
};
const edges = (filters = DEFAULT_FILTERS, scope = null) => Object.fromEntries(buildElements(graph, filters, scope)
  .filter((e) => e.group === 'edges').map((e) => [e.data.id, e]));
const has = (edge, cls) => edge.classes.split(' ').includes(cls);

test('sous-faction : arête de la faction membre vers sa faction parente', () => {
  const { 'mem-1': npc, 'mem-2': sub } = edges();
  assert.deepEqual([npc.data.source, npc.data.target], ['npc-1', 'faction-8']);
  assert.ok(!has(npc, 'faction-member'));
  assert.deepEqual([sub.data.source, sub.data.target, sub.data.label], ['faction-8', 'faction-7', 'Bras armé']);
  assert.ok(has(sub, 'membership') && has(sub, 'faction-member') && !has(sub, 'hidden'));
  assert.equal(sub.data.color, '#7c8796');
  // L'anneau d'un PNJ ne dépend que de ses propres factions.
  assert.deepEqual([...npcRingColors(graph)], [[1, '#4f7fb5']]);
});

test('sous-faction : masquée avec le filtre des appartenances ou avec l’une des deux factions', () => {
  assert.ok(has(edges({ ...DEFAULT_FILTERS, showMemberships: false })['mem-2'], 'hidden'));
  assert.ok(has(edges({ ...DEFAULT_FILTERS, hiddenFactions: [7] })['mem-2'], 'hidden'));
  const hiddenChild = edges({ ...DEFAULT_FILTERS, hiddenFactions: [8] });
  assert.ok(has(hiddenChild['mem-2'], 'hidden'));
  assert.ok(has(hiddenChild['mem-1'], 'hidden'));
});

test('sous-faction : carte d’un scénario (fiches citées, voisines en retrait)', () => {
  const scope = { nodes: new Set(['faction-8']), context: new Set(['faction-7']) };
  const sub = edges(DEFAULT_FILTERS, scope)['mem-2'];
  assert.ok(has(sub, 'context') && !has(sub, 'hidden'));
  assert.ok(has(edges(DEFAULT_FILTERS, { nodes: new Set(['faction-8']), context: null })['mem-2'], 'hidden'));
});
