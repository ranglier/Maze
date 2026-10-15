// util.js : identifiants des nœuds de la carte, texte sans accents, initiales et couleurs des portraits.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow } from './helpers.mjs';

fakeWindow();
const { initials, initialsAvatar, nodeId, normalize, parseNodeId, shade, storage } = await import('../src/util.js');

test('identifiant de nœud : aller-retour', () => {
  for (const [kind, id] of [['npc', 1], ['faction', 42], ['place', 123456]]) {
    assert.equal(nodeId(kind, id), `${kind}-${id}`);
    assert.deepEqual(parseNodeId(nodeId(kind, id)), { kind, id });
  }
});

test('normalize : sans accents ni casse, espaces de bord retirés', () => {
  assert.equal(normalize('  Élise de Pont-Àvèn '), 'elise de pont-aven');
  assert.equal(normalize('ŒUVRE ça'), 'œuvre ca');
  assert.equal(normalize(null), '');
  assert.equal(normalize(undefined), '');
});

test('initiales : premier et dernier mot, deux lettres au plus', () => {
  assert.equal(initials('Maëlis Corvin'), 'MC');
  assert.equal(initials('Capitaine Jean de la Mer'), 'CM');
  assert.equal(initials("Silas l'Ombre"), 'SO');
  assert.equal(initials('Vex'), 'V');
  assert.equal(initials(''), '?');
  assert.equal(initials('!!!'), '?');
});

test('shade : assombrit et éclaircit vers le noir et le blanc', () => {
  assert.equal(shade('#808080', 0), '#808080');
  assert.equal(shade('#808080', -1), '#000000');
  assert.equal(shade('#808080', 1), '#ffffff');
  assert.equal(shade('#204060', -0.5), '#102030');
  assert.match(shade('#c9a45c', 0.3), /^#[0-9a-f]{6}$/);
});

test('portrait par défaut : SVG en data URL, nom échappé', () => {
  const url = initialsAvatar('<Ëve> "X"', '#c9a45c');
  assert.ok(url.startsWith('data:image/svg+xml;charset=utf-8,'));
  const svg = decodeURIComponent(url.split(',')[1]);
  assert.ok(svg.includes('>ËX<'), svg);
  assert.ok(!svg.includes('<Ëve>'));
});

test('stockage : lecture et écriture sans exception, même indisponible', () => {
  storage.set('k', 'v');
  assert.equal(storage.get('k'), 'v');
  const broken = { getItem() { throw new Error('refusé'); }, setItem() { throw new Error('plein'); } };
  window.localStorage = broken;
  assert.equal(storage.get('k'), null);
  assert.doesNotThrow(() => storage.set('k', 'w'));
});
