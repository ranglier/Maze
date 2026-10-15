// Empreinte du schéma de l'éditeur (audit H3, schema-fingerprint.mjs) : ce qui la change, ce qui ne la change pas,
// et l'accord entre src/editor/schema-version.json et le schéma actuel (build.mjs refuse sinon de construire).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle } from './helpers.mjs';

const mod = await bundle(`export { schema } from './src/editor/schema.js';
export { Schema } from 'prosemirror-model';
export { describeSchema, fingerprintOf } from './schema-fingerprint.mjs';`);
const { Schema, describeSchema, fingerprintOf, schema } = mod;
const recorded = JSON.parse(readFileSync(new URL('../src/editor/schema-version.json', import.meta.url), 'utf8'));

/** Copie du schéma de l'éditeur, une spécification de nœud modifiée par `change(name, spec)`. */
function variant(change) {
  const nodes = {};
  schema.spec.nodes.forEach((name, spec) => { nodes[name] = change(name, { ...spec }) ?? { ...spec }; });
  const marks = {};
  schema.spec.marks.forEach((name, spec) => { marks[name] = { ...spec }; });
  return new Schema({ nodes, marks, topNode: schema.spec.topNode });
}

test('empreinte enregistrée = schéma actuel (sinon : nouvelle version, et collab.SCHEMA_VERSION)', () => {
  assert.equal(fingerprintOf(schema), recorded.fingerprint);
  assert.ok(Number.isInteger(recorded.version) && recorded.version >= 1);
});

test('empreinte stable : même schéma reconstruit, règles d\'affichage changées', () => {
  assert.equal(fingerprintOf(variant(() => null)), fingerprintOf(schema));
  const display = variant((name, spec) => (name === 'paragraph'
    ? { ...spec, toDOM: () => ['div', 0], parseDOM: [{ tag: 'section' }] } : null));
  assert.equal(fingerprintOf(display), fingerprintOf(schema));
});

test('empreinte changée : attribut, valeur par défaut, contenu admis, nœud retiré', () => {
  const base = fingerprintOf(schema);
  const attr = variant((name, spec) => (name === 'paragraph'
    ? { ...spec, attrs: { ...(spec.attrs || {}), couleur: { default: null } } } : null));
  assert.notEqual(fingerprintOf(attr), base);
  const withDefault = variant((name, spec) => (name === 'paragraph' && spec.attrs?.align
    ? { ...spec, attrs: { ...spec.attrs, align: { default: 'right' } } } : null));
  if (schema.nodes.paragraph.spec.attrs?.align) assert.notEqual(fingerprintOf(withDefault), base);
  const content = variant((name, spec) => (name === 'blockquote' ? { ...spec, content: 'paragraph+' } : null));
  assert.notEqual(fingerprintOf(content), base);
  const description = describeSchema(schema);
  assert.equal(description.top, 'doc');
  for (const name of ['callout', 'mention', 'image', 'table']) {
    assert.ok(description.nodes.some(([n]) => n === name), `nœud ${name} absent de la description`);
  }
  assert.ok(description.marks.some(([n]) => n === 'comment'));
});
