// trash.js : corbeille d'une campagne (délai avant effacement, effectifs et filtre par genre, adresse restaurée).
import test from 'node:test';
import assert from 'node:assert/strict';

const T = await import('../src/trash.js');

const NOW = Date.parse('2026-11-19T12:00:00Z');

test('dates du serveur : ISO avec Z, ou sans fuseau lue en UTC', () => {
  assert.equal(T.parseStamp('2026-11-19T12:00:00Z').getTime(), NOW);
  assert.equal(T.parseStamp('2026-11-19 12:00:00').getTime(), NOW);
  assert.equal(T.parseStamp('2026-11-19T13:00:00+01:00').getTime(), NOW);
  assert.equal(T.parseStamp(''), null);
  assert.equal(T.parseStamp(null), null);
  assert.equal(T.parseStamp('demain'), null);
});

test('jours restants avant l’effacement : arrondi au jour supérieur, jamais négatif', () => {
  assert.equal(T.daysLeft('2026-12-19T12:00:00Z', NOW), 30);
  assert.equal(T.daysLeft('2026-12-19T11:59:00Z', NOW), 30, 'un peu moins de 30 jours : 30');
  assert.equal(T.daysLeft('2026-11-20T11:00:00Z', NOW), 1);
  assert.equal(T.daysLeft('2026-11-19T12:30:00Z', NOW), 1, 'une demi-heure : 1 jour');
  assert.equal(T.daysLeft('2026-11-19T12:00:00Z', NOW), 0, 'échéance atteinte : 0');
  assert.equal(T.daysLeft('2026-11-01T00:00:00Z', NOW), 0, 'dépassée (purge pas encore passée) : 0');
  assert.equal(T.daysLeft('2026-12-19T12:00:00Z', new Date(NOW)), 30, 'maintenant donné en Date');
  assert.equal(T.daysLeft(undefined, NOW), null);
});

const items = [
  { id: 9, kind: 'event' }, { id: 8, kind: 'npc' }, { id: 7, kind: 'document' }, { id: 6, kind: 'npc' },
  { id: 5, kind: 'inconnu' }, { id: 4, kind: 'map' },
];

test('effectifs par genre, dans l’ordre des filtres (genre inconnu à la fin)', () => {
  assert.deepEqual(T.kindCounts(items), [
    { kind: 'npc', count: 2 }, { kind: 'document', count: 1 }, { kind: 'event', count: 1 }, { kind: 'map', count: 1 },
    { kind: 'inconnu', count: 1 },
  ]);
  assert.deepEqual(T.kindCounts([]), []);
  assert.deepEqual(T.kindCounts(null), []);
});

test('filtre par genre : ordre du serveur conservé, genre vide ou absent : tout', () => {
  assert.deepEqual(T.filterByKind(items, 'npc').map((i) => i.id), [8, 6]);
  assert.equal(T.filterByKind(items, ''), items);
  assert.equal(T.filterByKind(items, 'faction'), items, 'plus aucune faction (dernière restaurée) : tout');
  assert.deepEqual(T.filterByKind(null, 'npc'), []);
});

test('libellés et icônes des genres', () => {
  assert.deepEqual(T.KIND_ORDER, ['npc', 'faction', 'place', 'document', 'event', 'map']);
  assert.equal(T.kindLabel('document'), 'Fiche rédigée');
  assert.equal(T.kindLabel('autre'), 'autre');
  assert.equal(T.kindIcon('map'), 'map');
  assert.equal(T.kindIcon('autre'), 'trash');
});

test('adresse de l’élément restauré', () => {
  assert.equal(T.objectHref(3, 'npc', 12), '#/c/3/personnages/npc-12');
  assert.equal(T.objectHref(3, 'faction', 4), '#/c/3/personnages/faction-4');
  assert.equal(T.objectHref(3, 'place', 5), '#/c/3/lieux/5');
  assert.equal(T.objectHref(3, 'document', 6), '#/c/3/scenarios/6');
  assert.equal(T.objectHref(3, 'event', 7), '#/c/3/chronologie?event=7');
  assert.equal(T.objectHref(3, 'map', 8), '#/c/3/cartes/8');
  assert.equal(T.objectHref(3, 'relation', 8), null);
  assert.equal(T.objectHref(3, 'npc', null), null);
  assert.equal(T.objectHref(null, 'npc', 1), null);
});
