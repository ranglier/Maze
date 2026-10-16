// cast.js : distribution de la carte d'un scénario (castOf), comparée aux deux règles du serveur sur les campagnes
// aléatoires de fixtures/cast-cases.json (écrit et vérifié par backend/tests/test_cast_rules.py, audit QUAL-12).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { castOf, mentionedNodes, neighborsOf, scenariosOf } from '../src/cast.js';

const cases = JSON.parse(readFileSync(new URL('./fixtures/cast-cases.json', import.meta.url), 'utf8'));
const sorted = (set) => [...set].sort();

test('castOf = scenario_maps_of = scenarios_of (réglages de la route des mentions)', () => {
  let compared = 0;
  for (const c of cases) {
    for (const [sid, expected] of Object.entries(c.expected)) {
      assert.deepEqual(sorted(castOf(c.payload, Number(sid)).nodes), expected, `campagne ${c.campaign}, scénario ${sid}`);
      compared += 1;
    }
  }
  assert.ok(compared > 200, `${compared} scénarios`);
});

test('castOf avec les réglages chargés de la carte du scénario (GET /api/documents/{id}/map)', () => {
  for (const c of cases) {
    for (const [sid, expected] of Object.entries(c.expected)) {
      const { nodes } = castOf(c.payload, Number(sid), c.maps[sid]);
      assert.deepEqual(sorted(nodes), expected, `campagne ${c.campaign}, scénario ${sid}`);
    }
  }
});

test('scenariosOf : scénarios de la fiche de campagne, dans l\'ordre', () => {
  for (const c of cases) {
    const ids = scenariosOf(c.payload).map((d) => String(d.id));
    assert.deepEqual([...ids].sort(), Object.keys(c.expected).sort(), `campagne ${c.campaign}`);
  }
  assert.deepEqual(scenariosOf(null), []);
});

test('retraits et mentions : excluded ne garde que les fiches citées puis retirées', () => {
  const data = {
    root_id: 1,
    documents: [{ id: 1, parent_id: null, kind: 'campaign', sort_order: 0 },
      { id: 2, parent_id: 1, kind: 'scenario', sort_order: 0 }, { id: 3, parent_id: 2, kind: 'page', sort_order: 0 },
      { id: 4, parent_id: 3, kind: 'page', sort_order: 0 }],
    mentions: { 2: [['npc', 1]], 4: [['npc', 2], ['faction', 1], ['place', 9]] },
    overrides: { 2: [['npc', 2, 'excluded'], ['npc', 3, 'added'], ['npc', 4, 'excluded']] },
  };
  assert.deepEqual(sorted(mentionedNodes(data, 2)), ['faction-1', 'npc-1', 'npc-2']);   // lieux : pas sur la carte
  const cast = castOf(data, 2);
  assert.deepEqual(sorted(cast.nodes), ['faction-1', 'npc-1', 'npc-3']);
  assert.deepEqual(sorted(cast.excluded), ['npc-2']);
  assert.deepEqual(sorted(castOf(null, 2).nodes), []);
});

test('voisins : liens et appartenances autour des fiches affichées', () => {
  const graph = {
    relations: [{ source_kind: 'npc', source_id: 1, target_kind: 'npc', target_id: 2 },
      { source_kind: 'npc', source_id: 3, target_kind: 'npc', target_id: 4 }],
    memberships: [{ npc_id: 1, faction_id: 7 }],
  };
  assert.deepEqual(sorted(neighborsOf(graph, new Set(['npc-1']))), ['faction-7', 'npc-2']);
  assert.deepEqual(sorted(neighborsOf(graph, new Set(['npc-1', 'npc-2']))), ['faction-7']);
  // Sous-faction (member_faction_id) : voisine de sa faction parente, et inversement.
  graph.memberships.push({ npc_id: null, member_faction_id: 8, faction_id: 7 });
  assert.deepEqual(sorted(neighborsOf(graph, new Set(['faction-8']))), ['faction-7']);
  assert.deepEqual(sorted(neighborsOf(graph, new Set(['faction-7']))), ['faction-8', 'npc-1']);
});
