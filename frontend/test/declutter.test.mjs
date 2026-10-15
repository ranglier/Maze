// graph/declutter.js : finition de la disposition de la carte des relations (calcul pur, sans DOM).
import test from 'node:test';
import assert from 'node:assert/strict';
import { declutter, issues, layoutScore, overlap } from '../src/graph/declutter.js';
import { rng } from './helpers.mjs';

const box = (x, y, hw, hh, ux = 1, uy = 0) => ({ c: { x, y }, u: { x: ux, y: uy }, v: { x: -uy, y: ux }, hw, hh });
const total = (o) => Object.values(o).reduce((a, b) => a + b, 0);
const node = (id) => ({ id, w: 56, h: 56, labelW: 70, labelH: 14, labelGap: 5 });

/** Carte aléatoire serrée : fiches presque superposées, liens étiquetés. */
function crowded(seed, n = 12, m = 16) {
  const r = rng(seed);
  const nodes = Array.from({ length: n }, (_, i) => node(`n${i}`));
  const edges = [];
  for (let k = 0; k < m; k += 1) {
    const s = Math.floor(r() * n);
    let t = Math.floor(r() * n);
    if (t === s) t = (s + 1) % n;
    edges.push({ id: `e${k}`, s: `n${s}`, t: `n${t}`, labelW: 40 + Math.floor(r() * 60), labelH: 12, offset: 0 });
  }
  const pos = new Map(nodes.map((nd) => [nd.id, { x: r() * 120, y: r() * 120 }]));
  return { model: { nodes, edges }, pos };
}

test('overlap : séparation des rectangles orientés', () => {
  assert.equal(overlap(box(0, 0, 10, 10), box(30, 0, 10, 10)), null);
  const o = overlap(box(0, 0, 10, 10), box(15, 0, 10, 10));
  assert.ok(o && Math.abs(o.depth - 5) < 1e-9 && o.x > 0, JSON.stringify(o));
  // Simple contact (moins d'un demi-pixel) : pas un recouvrement.
  assert.equal(overlap(box(0, 0, 10, 10), box(19.6, 0, 10, 10)), null);
  // Rectangle tourné de 45° : la diagonale ne touche plus.
  const d = Math.SQRT1_2;
  assert.equal(overlap(box(0, 0, 10, 2, d, d), box(10, -10, 3, 3)), null);
  assert.ok(overlap(box(0, 0, 10, 2, d, d), box(5, 5, 3, 3)));
});

test('declutter : fiches écartées, problèmes de lisibilité réduits, rien de NaN', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const { model, pos } = crowded(seed);
    const before = issues(model, pos);
    const after = declutter(model, pos, { budgetMs: 5000 });
    for (const p of after.values()) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
    assert.equal(after.size, model.nodes.length);
    const left = issues(model, after);
    assert.ok(total(left) < total(before), `graine ${seed} : ${JSON.stringify(before)} -> ${JSON.stringify(left)}`);
    // Étiquettes posées sur une autre ou sur une fiche : presque toutes résolues (plus de 200 au départ).
    const labels = (o) => o.labelLabel + o.labelNode;
    assert.ok(labels(left) <= labels(before) / 20, `graine ${seed} : ${JSON.stringify(left)}`);
    assert.equal(left.short, 0, `graine ${seed} : liens trop courts pour leur étiquette`);
    assert.ok(layoutScore(model, after) < layoutScore(model, pos), `graine ${seed} : note`);
  }
});

test('declutter : positions d\'entrée intactes (nouvelle Map en sortie)', () => {
  const { model, pos } = crowded(9);
  const copy = new Map([...pos].map(([k, v]) => [k, { ...v }]));
  const out = declutter(model, pos, { iterations: 200 });
  assert.deepEqual(pos, copy);
  assert.notEqual(out, pos);
});

test('declutter : disposition déjà lisible laissée en place', () => {
  const nodes = [node('a'), node('b')];
  const edges = [{ id: 'ab', s: 'a', t: 'b', labelW: 40, labelH: 12, offset: 0 }];
  const pos = new Map([['a', { x: 0, y: 0 }], ['b', { x: 300, y: 0 }]]);
  const out = declutter({ nodes, edges }, pos);
  assert.deepEqual(issues({ nodes, edges }, out), { labelLabel: 0, labelNode: 0, short: 0, lineNode: 0, lineLabel: 0 });
  assert.ok(Math.abs(out.get('b').x - out.get('a').x - 300) < 1);
});

test('declutter : budget de temps respecté', () => {
  const { model, pos } = crowded(11, 60, 120);
  const start = Date.now();
  declutter(model, pos, { budgetMs: 200 });
  assert.ok(Date.now() - start < 2000, `${Date.now() - start} ms`);
});
