// imageSelection.js : sélection multiple d'Administration > Images (bascule, plage, tout sélectionner, nettoyage
// après rechargement), tri, récapitulatif d'une suppression groupée, découpage en lots et lecture des réponses.
import test from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../src/imageSelection.js');

const lib = (id, extra = {}) => ({ source: 'library', id, caption: '', name: `l${id}.png`, bytes: 100,
  created_at: '2026-10-01T10:00:00Z', ...extra });
const gal = (id, extra = {}) => ({ source: 'gallery', id, caption: '', name: '', bytes: 200, cover: false,
  owner: { kind: 'npc', id: 1, name: 'Hart' }, created_at: '2026-10-02T10:00:00Z', ...extra });
const doc = (id, docId, extra = {}) => ({ source: 'document', id, caption: '', name: '', bytes: 50, used: true,
  document: docId ? { id: docId, title: `Fiche ${docId}`, kind: 'scenario' } : null,
  created_at: '2026-10-03T10:00:00Z', ...extra });
const map = (id, extra = {}) => ({ source: 'map', id, caption: `Carte ${id}`, name: '', bytes: 1000,
  created_at: '2026-09-01T10:00:00Z', ...extra });

test('clé d’une image : sorte et identifiant', () => {
  assert.equal(S.imageKey(lib(3)), 'library-3');
  assert.equal(S.imageKey(gal(3)), 'gallery-3');
});

test('bascule : ajoute ou retire, sans modifier l’ancienne sélection', () => {
  const empty = new Set();
  const one = S.toggleKey(empty, 'library-1');
  assert.deepEqual([...one], ['library-1']);
  assert.equal(empty.size, 0, 'ancienne sélection intacte');
  const none = S.toggleKey(one, 'library-1');
  assert.equal(none.size, 0);
  assert.deepEqual([...one], ['library-1']);
});

test('plage dans l’ordre affiché, dans les deux sens', () => {
  const order = ['a', 'b', 'c', 'd', 'e'];
  assert.deepEqual(S.rangeKeys(order, 'b', 'd'), ['b', 'c', 'd']);
  assert.deepEqual(S.rangeKeys(order, 'd', 'b'), ['b', 'c', 'd']);
  assert.deepEqual(S.rangeKeys(order, 'c', 'c'), ['c']);
  assert.deepEqual(S.rangeKeys(order, null, 'c'), ['c'], 'sans ancre : la vignette seule');
  assert.deepEqual(S.rangeKeys(order, 'zz', 'c'), ['c'], 'ancre masquée par le filtre : la vignette seule');
  assert.deepEqual(S.rangeKeys(order, 'a', 'zz'), [], 'cible inconnue : rien');
  const sel = S.addRange(new Set(['e']), order, 'a', 'c');
  assert.deepEqual([...sel].sort(), ['a', 'b', 'c', 'e'], 'la plage s’ajoute à la sélection');
});

test('tout sélectionner : union avec la sélection (images masquées gardées)', () => {
  const sel = S.selectAll(new Set(['x']), ['a', 'b']);
  assert.deepEqual([...sel].sort(), ['a', 'b', 'x']);
});

test('nettoyage après rechargement : clés disparues retirées, même objet si rien ne change', () => {
  const images = [lib(1), gal(2)];
  const kept = new Set(['library-1', 'gallery-2']);
  assert.equal(S.pruneSelection(kept, images), kept, 'inchangée : même objet (pas de rendu inutile)');
  const pruned = S.pruneSelection(new Set(['library-1', 'library-9']), images);
  assert.deepEqual([...pruned], ['library-1']);
  assert.equal(S.pruneSelection(new Set(['library-1']), null).size, 0, 'liste absente : sélection vide');
});

test('images sélectionnées masquées par le filtre', () => {
  const sel = new Set(['a', 'b', 'c']);
  assert.equal(S.hiddenCount(sel, ['a', 'z']), 2);
  assert.equal(S.hiddenCount(sel, new Set(['a', 'b', 'c'])), 0);
});

test('tri : récentes, anciennes, lourdes, nom', () => {
  const title = (img) => img.caption || img.name;
  const images = [
    lib(1, { created_at: '2026-10-01T10:00:00Z', bytes: 300, caption: 'Épée' }),
    lib(2, { created_at: '2026-10-05T10:00:00Z', bytes: 100, caption: 'arbre' }),
    gal(3, { created_at: '2026-10-03T10:00:00Z', bytes: 900, caption: 'Zeppelin' }),
    lib(4, { created_at: '2026-10-05T10:00:00Z', bytes: 100, caption: 'Château 10' }),
    lib(5, { created_at: '2026-10-02T10:00:00Z', bytes: 100, caption: 'Château 9' }),
  ];
  const ids = (list) => list.map((i) => i.id);
  assert.deepEqual(ids(S.sortImages(images, 'recent', title)), [4, 2, 3, 5, 1], 'même date : plus grand id d’abord');
  assert.deepEqual(ids(S.sortImages(images, 'oldest', title)), [1, 5, 3, 2, 4]);
  assert.deepEqual(ids(S.sortImages(images, 'heaviest', title)), [3, 1, 4, 2, 5], 'même taille : plus récente d’abord');
  assert.deepEqual(ids(S.sortImages(images, 'name', title)), [2, 5, 4, 1, 3], 'ordre alphabétique, accents et nombres');
  assert.deepEqual(ids(images), [1, 2, 3, 4, 5], 'liste d’origine intacte');
  assert.deepEqual(ids(S.sortImages(images, 'inconnu', title)), [4, 2, 3, 5, 1], 'tri inconnu : plus récentes');
  assert.equal(S.normalizeSort('heaviest'), 'heaviest');
  assert.equal(S.normalizeSort('n’importe quoi'), 'recent');
  assert.equal(S.normalizeSort(null), 'recent');
  assert.deepEqual(S.SORTS.map((s) => s.key), ['recent', 'oldest', 'heaviest', 'name']);
});

test('récapitulatif de suppression : sortes, couvertures, illustrations affichées, cartes', () => {
  const picked = [
    lib(1), lib(2),
    gal(3, { cover: true }), gal(4), gal(5, { cover: true, owner: { kind: 'place', id: 7, name: 'Ys' } }),
    doc(6, 10), doc(7, 10), doc(8, 11), doc(9, 12, { used: false }), doc(10, null, { used: false }),
    map(11),
  ];
  const s = S.deletionSummary(picked);
  assert.equal(s.count, 11);
  assert.equal(s.bytes, 2 * 100 + 3 * 200 + 5 * 50 + 1000);
  assert.deepEqual(s.bySource, { library: 2, gallery: 3, document: 5, map: 1 });
  assert.equal(s.covers, 2);
  assert.equal(s.shownInDocs, 3, 'illustrations encore dans le texte');
  assert.equal(s.documents, 2, 'fiches rédigées distinctes touchées');
  assert.equal(s.maps, 1);
  const none = S.deletionSummary([]);
  assert.equal(none.count, 0);
  assert.deepEqual(none.bySource, { library: 0, gallery: 0, document: 0, map: 0 });
});

test('découpage en lots de 200 au plus, éléments {source, id}', () => {
  const images = Array.from({ length: 450 }, (_, i) => lib(i + 1));
  const batches = S.chunks(S.toItems(images));
  assert.deepEqual(batches.map((b) => b.length), [200, 200, 50]);
  assert.deepEqual(batches[0][0], { source: 'library', id: 1 });
  assert.deepEqual(batches[2][49], { source: 'library', id: 450 });
  assert.equal(S.BATCH_SIZE, 200);
  assert.deepEqual(S.chunks([]), []);
  assert.deepEqual(S.chunks([1, 2, 3], 2), [[1, 2], [3]]);
});

test('réponses des lots : total supprimé, images laissées car en cours d’écriture, absentes ignorées', () => {
  const out = S.mergeResults([
    { deleted: 198, by_source: { library: 198 }, documents: [], skipped: [
      { source: 'document', id: 4, reason: 'editing' }, { source: 'library', id: 9, reason: 'missing' }] },
    { deleted: 3, by_source: { gallery: 3 }, documents: [5], skipped: [{ source: 'document', id: 8, reason: 'editing' }] },
    { deleted: 0 },
  ]);
  assert.equal(out.deleted, 201);
  assert.deepEqual(out.editing, ['document-4', 'document-8']);
  assert.equal(out.missing, 1);
});
