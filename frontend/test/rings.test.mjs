// Anneaux des sous-lieux d'une carte interactive (components/maps/rings.js, sorti de MapsView.jsx, audit QUAL-10) :
// pages de 10 puces, frères repliés en « +N autres », chaîne qui part vers l'extérieur, et la mesure annoncée dans
// ARCHITECTURE.md (3000 arbres aléatoires : puces voisines et anneaux différents jamais superposés).
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow, rng } from './helpers.mjs';

fakeWindow();
const { CHIP_H, PAGE, layoutRings } = await import('../src/components/maps/rings.js');

const screen = (x, y) => ({ x: x * 1000, y: y * 800 });
const noExtras = () => 0;
const place = (id, parent_id, name = `Lieu ${String(id).padStart(2, '0')}`) => ({ id, parent_id, name, sort_order: 0, kind: 'lieu' });
const chips = (rings) => rings.flatMap((r) => r.items);

/** Arbre aléatoire sous un lieu épinglé (id 1) et un chemin d'anneaux qui y descend. */
function randomCase(r) {
  const places = [place(1, null, 'Racine')];
  let next = 2;
  const grow = (parent, depth) => {
    const n = depth === 0 ? 1 + Math.floor(r() * 14) : Math.floor(r() * (depth > 2 ? 4 : 9));
    for (let i = 0; i < n; i += 1) {
      const id = next;
      next += 1;
      const name = 'Nom'.padEnd(4 + Math.floor(r() * 13), 'x');   // 4 à 16 caractères
      places.push(place(id, parent, name));
      if (depth < 4 && r() < 0.5) grow(id, depth + 1);
    }
  };
  grow(1, 0);
  const path = [1];
  for (let level = 0; level < 5; level += 1) {
    const kids = places.filter((p) => p.parent_id === path[path.length - 1]);
    if (!kids.length || r() < 0.2) break;
    path.push(kids[Math.floor(r() * kids.length)].id);
  }
  return { places, path, pins: [{ place_id: 1, x: 0.2 + r() * 0.6, y: 0.2 + r() * 0.6 }] };
}

const overlaps = (a, b) => Math.abs(a.x - b.x) < (a.w + b.w) / 2 && Math.abs(a.y - b.y) < CHIP_H - 2;

test('premier anneau : centré sur l\'épingle, tous les sous-lieux', () => {
  const places = [place(1, null), ...[2, 3, 4].map((id) => place(id, 1))];
  const rings = layoutRings([1], [{ place_id: 1, x: 0.5, y: 0.5 }], places, screen, {}, noExtras, {});
  assert.equal(rings.length, 1);
  assert.deepEqual([rings[0].cx, rings[0].cy], [500, 400]);
  assert.deepEqual(rings[0].items.map((it) => it.place.id).sort(), [2, 3, 4]);
  assert.equal(rings[0].full, true);
  for (const it of rings[0].items) assert.ok(Math.abs(Math.hypot(it.x - 500, it.y - 400) - rings[0].radius) < 1e-6);
  assert.deepEqual(layoutRings([9], [{ place_id: 1, x: 0.5, y: 0.5 }], places, screen, {}, noExtras, {}), []);
});

test('pages : PAGE - 1 puces et « +N », page suivante demandée', () => {
  const places = [place(1, null), ...Array.from({ length: 12 }, (_, i) => place(i + 2, 1))];
  const pin = [{ place_id: 1, x: 0.5, y: 0.5 }];
  const first = layoutRings([1], pin, places, screen, {}, noExtras, {})[0].items;
  assert.equal(first.length, PAGE);
  assert.equal(first.filter((it) => it.place).length, PAGE - 1);
  const more = first.find((it) => it.more);
  assert.equal(more.more, 12 - (PAGE - 1));
  const second = layoutRings([1], pin, places, screen, { 1: more.nextPage }, noExtras, {})[0].items;
  assert.deepEqual(second.filter((it) => it.place).map((it) => it.place.id), [11, 12, 13]);
});

test('frères repliés : lieu choisi qui a des sous-lieux, « +N autres », redéployés sur demande', () => {
  const places = [place(1, null), place(2, 1), place(3, 1), place(4, 1), place(5, 2), place(6, 2)];
  const pin = [{ place_id: 1, x: 0.5, y: 0.5 }];
  const rings = layoutRings([1, 2], pin, places, screen, {}, noExtras, {});
  assert.equal(rings.length, 2);
  assert.equal(rings[0].items.length, 2);
  assert.equal(rings[0].items.find((it) => it.expand).count, 2);
  assert.deepEqual(rings[1].items.map((it) => it.place.id).sort(), [5, 6]);
  const chosen = rings[0].items.find((it) => it.place?.id === 2);
  assert.deepEqual([rings[1].cx, rings[1].cy], [chosen.x, chosen.y]);   // le lieu choisi au centre de son anneau
  const open = layoutRings([1, 2], pin, places, screen, {}, noExtras, { 1: true });
  assert.equal(open.length, 1);                                          // anneaux plus profonds masqués
  assert.equal(open[0].items.length, 3);
  // Parent à un seul sous-lieu : pas de repli, l'anneau suivant est un arc.
  const single = layoutRings([1, 2], pin, [place(1, null), place(2, 1), place(5, 2)], screen, {}, noExtras, {});
  assert.equal(single.length, 2);
  assert.equal(single[1].full, false);
});

test('3000 arbres aléatoires : puces voisines et anneaux différents jamais superposés', () => {
  // Mesure de non-régression (algorithme inchangé par le découpage) : sur un petit premier anneau, deux puces
  // opposées aux noms longs peuvent se toucher (2,9 % des chaînes avec cette graine) ; jamais deux puces voisines
  // d'un même anneau (ringRadius), jamais deux puces d'anneaux différents.
  const r = rng(2026);
  let chains = 0;
  let crowded = 0;
  for (let n = 0; n < 3000; n += 1) {
    const { places, path, pins } = randomCase(r);
    const rings = layoutRings(path, pins, places, screen, {}, noExtras, {});
    if (!rings.length) continue;
    chains += 1;
    let hit = false;
    rings.forEach((ra, i) => rings.forEach((rb, j) => {
      if (j < i) return;
      ra.items.forEach((a, ia) => rb.items.forEach((b, ib) => {
        if ((i === j && ib <= ia) || !overlaps(a, b)) return;
        assert.ok(i === j, `chaîne ${n} : puces de deux anneaux superposées`);
        const k = ra.items.length;
        assert.ok(Math.abs(ia - ib) !== 1 && Math.abs(ia - ib) !== k - 1, `chaîne ${n} : puces voisines superposées`);
        hit = true;
      }));
    }));
    for (const it of chips(rings)) assert.ok(Number.isFinite(it.x) && Number.isFinite(it.y));
    if (hit) crowded += 1;
  }
  assert.ok(chains > 2500, `${chains} chaînes`);
  assert.ok(crowded / chains < 0.05, `${crowded} chaînes sur ${chains} avec un chevauchement (${(100 * crowded / chains).toFixed(1)} %)`);
});
