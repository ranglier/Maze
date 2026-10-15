// places.js : arborescence des lieux (arbre, fil d'Ariane, regroupement par type, filtre, récents).
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow } from './helpers.mjs';

fakeWindow();
const P = await import('../src/places.js');

const place = (id, parent_id, name, kind = 'lieu', extra = {}) => ({ id, parent_id, name, kind, sort_order: 0, ...extra });
const tree = [
  place(1, null, 'Ys', 'ville'),
  place(2, 1, 'Basse ville', 'quartier', { sort_order: 2 }),
  place(3, 1, 'Port', 'quartier', { sort_order: 1 }),
  place(4, 2, 'Taverne du Hareng', 'batiment', { tags: ['Contrebande'] }),
  place(5, 4, 'Cave', 'salle'),
  place(6, null, 'Baie engloutie', 'nature'),
];

test('enfants triés par ordre puis par nom (français)', () => {
  assert.deepEqual(P.childrenOf(tree, 1).map((p) => p.id), [3, 2]);
  assert.deepEqual(P.childrenOf(tree, null).map((p) => p.id), [6, 1]);   // ordre 0 : alphabétique
  const map = P.childrenMap(tree);
  assert.deepEqual(map.get(null).map((p) => p.id), [6, 1]);
  assert.deepEqual(map.get(1).map((p) => p.id), [3, 2]);
  const accents = [place(10, null, 'Été'), place(11, null, 'Eau'), place(12, null, 'Zinc')];
  assert.deepEqual(P.childrenOf(accents, null).map((p) => p.name), ['Eau', 'Été', 'Zinc']);
});

test('sous-arbre, chemin et ancêtres', () => {
  assert.deepEqual([...P.subtreeIds(tree, 2)].sort(), [2, 4, 5]);
  assert.deepEqual([...P.subtreeIds(tree, 6)], [6]);
  assert.deepEqual(P.pathIds(tree, 5), [5, 4, 2, 1]);
  const byId = new Map(tree.map((p) => [p.id, p]));
  assert.deepEqual(P.pathIds(byId, 3), [3, 1]);
  assert.deepEqual(P.ancestorsOf(byId, byId.get(5)).map((p) => p.id), [1, 2, 4]);
  assert.deepEqual(P.ancestorsOf(byId, byId.get(1)), []);
});

test('chemin borné même sur un cycle (données corrompues)', () => {
  const cycle = [place(1, 2, 'A'), place(2, 1, 'B')];
  assert.ok(P.pathIds(cycle, 1).length <= 1000);
  assert.ok(P.ancestorsOf(new Map(cycle.map((p) => [p.id, p])), cycle[0]).length <= 30);
  assert.deepEqual([...P.subtreeIds(cycle, 1)].sort(), [1, 2]);
});

test('regroupement par type : à partir de GROUP_MIN, types isolés dans « Autres types »', () => {
  const kinds = [{ key: 'batiment' }, { key: 'rue' }, { key: 'salle' }];
  const make = (n, kind, start) => Array.from({ length: n }, (_, i) => place(start + i, 1, `${kind} ${i}`, kind));
  assert.equal(P.groupsOf(1, make(P.GROUP_MIN - 1, 'rue', 100), kinds), null);   // liste courte
  assert.equal(P.groupsOf(1, make(P.GROUP_MIN, 'rue', 100), kinds), null);       // un seul groupe
  const kids = [...make(6, 'rue', 100), ...make(5, 'batiment', 200), place(300, 1, 'Seule', 'salle'),
    place(301, 1, 'Autre', 'inconnu')];
  const groups = P.groupsOf(1, kids, kinds);
  assert.deepEqual(groups.map((g) => g.kind), ['batiment', 'rue', P.OTHER_KINDS]);
  assert.deepEqual(groups.map((g) => g.items.length), [5, 6, 2]);
  assert.equal(groups[0].key, 'g:1:batiment');
  assert.equal(P.groupsOf(null, kids, kinds)[2].key, 'g:0:*');
  assert.ok(P.isGroupKey('g:1:rue'));
  assert.ok(!P.isGroupKey(12));
  assert.equal(P.groupParent('g:7:rue'), 7);
  assert.equal(P.groupParent('g:0:*'), null);
});

test('filtre : nom ou étiquette, sans accents, type facultatif', () => {
  assert.deepEqual(P.matchPlaces(tree, 'HARENG', '').map((p) => p.id), [4]);
  assert.deepEqual(P.matchPlaces(tree, 'contreband', '').map((p) => p.id), [4]);
  assert.deepEqual(P.matchPlaces(tree, '', 'quartier').map((p) => p.id), [2, 3]);
  assert.deepEqual(P.matchPlaces(tree, 'ville', 'ville').map((p) => p.id), []);   // « Ys » n'a pas « ville »
  assert.deepEqual(P.matchPlaces([place(9, null, 'Église')], 'egl', '').map((p) => p.id), [9]);
});

test('surlignage : découpe autour du texte trouvé', () => {
  assert.deepEqual(P.splitMatch('Basse ville', 'VILLE'), ['Basse ', 'ville', '']);
  assert.deepEqual(P.splitMatch('Église', 'egl'), ['', 'Égl', 'ise']);
  assert.equal(P.splitMatch('Port', 'zz'), null);
  assert.equal(P.splitMatch('Port', '  '), null);
  assert.equal(P.splitMatch('Œil', 'oe'), null);   // longueur changée par la normalisation : pas de surlignage
});

test('lieux récents : 8 au plus, sans doublon, le dernier en tête, par campagne', () => {
  for (let i = 1; i <= 10; i += 1) P.rememberPlace(1, i);
  P.rememberPlace(1, 5);
  assert.deepEqual(P.recentPlaces(1), [5, 10, 9, 8, 7, 6, 4, 3]);
  assert.deepEqual(P.recentPlaces(2), []);
  window.localStorage.setItem('maze:places:recent:3', '{"pas": "une liste"}');
  assert.deepEqual(P.recentPlaces(3), []);
  window.localStorage.setItem('maze:places:recent:4', 'illisible');
  assert.deepEqual(P.recentPlaces(4), []);
});
