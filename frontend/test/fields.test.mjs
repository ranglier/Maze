// Champs des fiches dans l'interface (fields.js) : dérivés du registre du serveur (entry-fields.generated.js, audit
// M23). Le registre lui-même est vérifié côté serveur (tests.test_registry : fichier à jour, colonnes, déclencheurs).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ENTITY_FIELDS, ENTRY_REGISTRY, PLACE_EDITABLE, PLACE_SECTIONS, UI_EXTRAS, editableKeys, fieldLabels, historyIdentity,
  textSections,
} from '../src/fields.js';

const KINDS = ['npc', 'faction', 'place'];
const keysOf = (list) => list.map((f) => f.key);
const where = (kind, pred) => ENTRY_REGISTRY[kind].filter(pred).map((f) => f.key);

test('registre : trois sortes de fiches, un nom en tête de chacune', () => {
  assert.deepEqual(Object.keys(ENTRY_REGISTRY).sort(), [...KINDS].sort());
  for (const kind of KINDS) assert.equal(ENTRY_REGISTRY[kind][0].type, 'name', kind);
});

test('compléments de l\'interface : seulement pour des champs du registre', () => {
  for (const [kind, extras] of Object.entries(UI_EXTRAS)) {
    for (const key of Object.keys(extras)) {
      assert.ok(ENTRY_REGISTRY[kind].some((f) => f.key === key), `${kind}.${key} absent du registre`);
    }
  }
});

test('formulaire des PNJ et factions : tous les champs modifiables, aucun ancien champ', () => {
  for (const kind of ['npc', 'faction']) {
    const editable = where(kind, (f) => f.group !== 'legacy');
    assert.deepEqual(new Set(editableKeys(kind)), new Set(editable), kind);
    assert.deepEqual(keysOf(ENTITY_FIELDS[kind].identity), where(kind, (f) => f.group === 'identity'));
    assert.deepEqual(keysOf(ENTITY_FIELDS[kind].sections), where(kind, (f) => f.group === 'section'));
    assert.equal(ENTITY_FIELDS[kind].identity.find((f) => f.key === 'name').required, true);
    for (const s of ENTITY_FIELDS[kind].sections) {
      assert.equal(Boolean(s.secret), ENTRY_REGISTRY[kind].find((f) => f.key === s.key).secret, `${kind}.${s.key}`);
    }
  }
  for (const key of ['people', 'age', 'location']) assert.ok(!editableKeys('npc').includes(key), key);
});

test('lieux : formulaire, rubriques et libellés', () => {
  assert.deepEqual(PLACE_EDITABLE, where('place', (f) => f.group !== 'legacy'));
  assert.deepEqual(keysOf(PLACE_SECTIONS), where('place', (f) => f.group === 'section'));
  assert.equal(PLACE_SECTIONS.find((f) => f.key === 'secrets').secret, true);
  assert.equal(fieldLabels('place').parent_id, 'Rangé dans');
});

test('sections commentables : celles du registre (comment)', () => {
  for (const kind of KINDS) {
    assert.deepEqual(new Set(keysOf(textSections(kind))), new Set(where(kind, (f) => f.comment)), kind);
  }
});

test('aperçu d\'une version : en-tête suivi par l\'historique, anciens champs compris', () => {
  assert.deepEqual(keysOf(historyIdentity('npc')), ['title', 'status', 'people', 'age', 'location']);
  assert.deepEqual(keysOf(historyIdentity('faction')), ['kind', 'color']);
  assert.deepEqual(keysOf(historyIdentity('place')), ['kind']);   // rangement : hors historique
});
