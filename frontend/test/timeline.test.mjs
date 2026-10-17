// timeline.js : chronologie (ordre manuel, insertion, « Déplacer vers… », filtre, couleur des étiquettes).
import test from 'node:test';
import assert from 'node:assert/strict';

const T = await import('../src/timeline.js');

const ev = (id, extra = {}) => ({ id, title: `É${id}`, date_label: '', tags: [], mentions: [], ...extra });
const events = [ev(1, { tags: ['Guerre'] }), ev(2, { tags: ['guerre', 'Cour'] }), ev(3), ev(4, { tags: ['Cour'] })];

test('déplacement avant un événement, ou à la fin', () => {
  const ids = [1, 2, 3, 4];
  assert.deepEqual(T.moveBefore(ids, 4, 1), [4, 1, 2, 3]);
  assert.deepEqual(T.moveBefore(ids, 1, 3), [2, 1, 3, 4]);
  assert.deepEqual(T.moveBefore(ids, 2, null), [1, 3, 4, 2]);
  assert.deepEqual(T.moveBefore(ids, 2, 2), ids, 'avant lui-même : inchangé');
  assert.deepEqual(T.moveBefore(ids, 9, 1), ids, 'identifiant inconnu : inchangé');
  assert.deepEqual(T.moveBefore(ids, 1, 9), ids, 'cible inconnue : inchangé');
  assert.notEqual(T.moveBefore(ids, 1, 3), ids, 'nouvelle liste, l’ancienne n’est pas modifiée');
  assert.deepEqual(ids, [1, 2, 3, 4]);
});

test('glisser-déposer : avant ou après la cible', () => {
  const ids = [1, 2, 3, 4];
  assert.deepEqual(T.moveRelative(ids, 1, 3, 'after'), [2, 3, 1, 4]);
  assert.deepEqual(T.moveRelative(ids, 1, 4, 'after'), [2, 3, 4, 1]);
  assert.deepEqual(T.moveRelative(ids, 4, 2, 'before'), [1, 4, 2, 3]);
  assert.deepEqual(T.moveRelative(ids, 3, 2, 'after'), [1, 2, 3, 4], 'déjà juste après : inchangé');
  assert.deepEqual(T.moveRelative(ids, 2, 2, 'after'), ids);
});

test('déplacement à un rang (borné)', () => {
  assert.deepEqual(T.moveToIndex([1, 2, 3], 3, 0), [3, 1, 2]);
  assert.deepEqual(T.moveToIndex([1, 2, 3], 1, 99), [2, 3, 1]);
  assert.deepEqual(T.moveToIndex([1, 2, 3], 2, -4), [2, 1, 3]);
});

test('insertion : before_id et liste locale', () => {
  assert.equal(T.beforeIdAt(events, 0), 1, 'au début : avant le premier');
  assert.equal(T.beforeIdAt(events, 2), 3, 'entre le 2e et le 3e : avant le 3e');
  assert.equal(T.beforeIdAt(events, 4), null, 'en fin : sans before_id');
  assert.equal(T.beforeIdAt([], 0), null);
  assert.deepEqual(T.idsOf(T.insertBefore(events, ev(9), 3)), [1, 2, 9, 3, 4]);
  assert.deepEqual(T.idsOf(T.insertBefore(events, ev(9), null)), [1, 2, 3, 4, 9]);
  assert.deepEqual(T.idsOf(T.insertBefore(events, ev(9), 42)), [1, 2, 3, 4, 9], 'cible disparue : à la fin');
});

test('« Déplacer vers… » : places proposées et place actuelle', () => {
  assert.deepEqual(T.moveSlots(events, 2), [
    { beforeId: 1, current: false }, { beforeId: 3, current: true }, { beforeId: 4, current: false },
    { beforeId: null, current: false },
  ]);
  const last = T.moveSlots(events, 4);
  assert.deepEqual(last.at(-1), { beforeId: null, current: true }, 'dernier : « À la fin » est sa place');
  assert.equal(last.filter((s) => s.current).length, 1);
  assert.deepEqual(T.moveSlots(events, 99), []);
  // Chaque place donne bien la liste attendue.
  assert.deepEqual(T.moveBefore(T.idsOf(events), 2, null), [1, 3, 4, 2]);
});

test('étiquettes : liste sans doublon et filtre (casse ignorée)', () => {
  assert.deepEqual(T.allTags(events), ['Cour', 'Guerre']);
  assert.deepEqual(T.idsOf(T.filterByTag(events, 'GUERRE')), [1, 2]);
  assert.deepEqual(T.idsOf(T.filterByTag(events, 'cour')), [2, 4]);
  assert.deepEqual(T.idsOf(T.filterByTag(events, '')), [1, 2, 3, 4]);
  assert.deepEqual(T.filterByTag(events, 'absente'), []);
});

test('couleur d’étiquette stable, calculée depuis le texte', () => {
  assert.equal(T.tagHue('Guerre'), T.tagHue('Guerre'));
  assert.equal(T.tagHue('Guerre'), T.tagHue('guerre'), 'casse ignorée');
  assert.notEqual(T.tagHue('Guerre'), T.tagHue('Cour'));
  for (const tag of ['', 'a', 'Séance 4', '🐉']) {
    const h = T.tagHue(tag);
    assert.ok(Number.isInteger(h) && h >= 0 && h < 360, `teinte de « ${tag} » : ${h}`);
  }
  assert.match(T.tagColor('Cour'), /^hsl\(\d+ 52% 64%\)$/);
  // Valeur figée : une autre formule changerait la couleur de toutes les étiquettes existantes.
  assert.equal(T.tagHue('Guerre'), T.tagHue(String('Guerre')));
});

test('fiches citées et adresses', () => {
  assert.deepEqual(T.eventMentions({ mentions: [['npc', 3], ['place', '7'], ['npc', 3], ['doc', 2]] }),
    [{ kind: 'npc', id: 3 }, { kind: 'place', id: 7 }, { kind: 'doc', id: 2 }]);
  assert.deepEqual(T.eventMentions({}), []);
  assert.equal(T.mentionHref(5, 'npc', 3), '#/c/5/personnages/npc-3');
  assert.equal(T.mentionHref(5, 'faction', 4), '#/c/5/personnages/faction-4');
  assert.equal(T.mentionHref(5, 'place', 7), '#/c/5/lieux/7');
  assert.equal(T.mentionHref(5, 'doc', 2), '#/c/5/scenarios/2');
  assert.equal(T.mentionHref(5, 'autre', 2), null);
  assert.equal(T.eventHref(5, 12), '#/c/5/chronologie?event=12');
});

test('description longue repliée', () => {
  assert.equal(T.isLongDescription(''), false);
  assert.equal(T.isLongDescription('<p>Court.</p>'), false);
  assert.equal(T.isLongDescription(`<p>${'mot '.repeat(80)}</p>`), true);
  assert.equal(T.isLongDescription('<p>a</p><p>b</p><p>c</p><p>d</p>'), true);
  assert.equal(T.plainText('<p>Un&nbsp;<strong>deux</strong></p>'), 'Un deux');
});

test('frise : carte suivante au clavier', () => {
  assert.equal(T.nextIndex(0, 'ArrowRight', 3), 1);
  assert.equal(T.nextIndex(2, 'ArrowRight', 3), 2, 'bornée à la dernière');
  assert.equal(T.nextIndex(0, 'ArrowLeft', 3), 0);
  assert.equal(T.nextIndex(1, 'Home', 3), 0);
  assert.equal(T.nextIndex(0, 'End', 3), 2);
  assert.equal(T.nextIndex(0, 'Enter', 3), null);
  assert.equal(T.nextIndex(0, 'ArrowRight', 0), null);
});

test('mode d’affichage : choix gardé, sinon liste sur téléphone en portrait', () => {
  assert.equal(T.initialMode('list', false), 'list');
  assert.equal(T.initialMode('frieze', true), 'frieze');
  assert.equal(T.initialMode(null, true), 'list');
  assert.equal(T.initialMode(null, false), 'frieze');
  assert.equal(T.initialMode('nimporte', false), 'frieze');
});

test('liste compacte : 3 pastilles d’étiquettes au plus, puis « +n »', () => {
  assert.deepEqual(T.tagDots(['a', 'b']), { shown: ['a', 'b'], more: 0 });
  assert.deepEqual(T.tagDots(['a', 'b', 'c']), { shown: ['a', 'b', 'c'], more: 0 });
  assert.deepEqual(T.tagDots(['a', 'b', 'c', 'd', 'e']), { shown: ['a', 'b', 'c'], more: 2 });
  assert.deepEqual(T.tagDots(undefined), { shown: [], more: 0 });
  assert.deepEqual(T.tagDots(['a', 'b'], 1), { shown: ['a'], more: 1 });
});

test('densité gardée séparément pour la liste et la frise', () => {
  assert.equal(T.densityKey('frieze'), 'maze:timeline:density', 'frise : clé d’avant, choix déjà fait conservé');
  assert.equal(T.densityKey('list'), 'maze:timeline:density:list');
  assert.notEqual(T.densityKey('list'), T.densityKey('frieze'));
  assert.equal(T.densityKey('autre'), T.densityKey('list'));
  assert.equal(T.initialDensity('compact'), 'compact');
  assert.equal(T.initialDensity('normal'), 'normal');
  assert.equal(T.initialDensity(null), 'normal');
  assert.equal(T.initialDensity('serré'), 'normal');
  assert.deepEqual(T.DENSITIES, ['normal', 'compact']);
});

test('mini-carte : rectangle de la partie visible', () => {
  const view = { scrollLeft: 0, clientWidth: 500, scrollWidth: 2000 };
  assert.deepEqual(T.minimapWindow(view), { left: 0, width: 0.25 });
  assert.deepEqual(T.minimapWindow({ ...view, scrollLeft: 1500 }), { left: 0.75, width: 0.25 });
  assert.deepEqual(T.minimapWindow({ ...view, scrollLeft: 1800 }), { left: 0.75, width: 0.25 }, 'borné à la fin');
  assert.deepEqual(T.minimapWindow({ scrollLeft: 0, clientWidth: 800, scrollWidth: 600 }), { left: 0, width: 1 });
  assert.deepEqual(T.minimapWindow({}), { left: 0, width: 1 });
  assert.equal(T.overflows(view), true);
  assert.equal(T.overflows({ scrollLeft: 0, clientWidth: 800, scrollWidth: 800 }), false, 'tout tient : pas de mini-carte');
});

test('mini-carte : clic, glissement et clavier vers scrollLeft', () => {
  const view = { scrollLeft: 300, clientWidth: 500, scrollWidth: 2000 };
  assert.equal(T.scrollForFraction(0.5, view), 750, 'clic au milieu : frise centrée');
  assert.equal(T.scrollForFraction(0, view), 0);
  assert.equal(T.scrollForFraction(0.98, view), 1500, 'près de la fin : tout au bout');
  assert.equal(T.scrollForDrag(300, 50, 400, view), 550, '50 px sur 400 : un huitième de 2000');
  assert.equal(T.scrollForDrag(300, -400, 400, view), 0);
  assert.equal(T.scrollForDrag(300, 10, 0, view), 300);
  assert.equal(T.sliderScroll('ArrowRight', view), 800, 'flèche : un écran');
  assert.equal(T.sliderScroll('ArrowLeft', view), 0);
  assert.equal(T.sliderScroll('End', view), 1500);
  assert.equal(T.sliderScroll('Home', view), 0);
  assert.equal(T.sliderScroll('ArrowRight', { ...view, scrollLeft: 1400 }), 1500, 'borné');
  assert.equal(T.sliderScroll('Enter', view), null);
  assert.equal(T.scrollPercent(view), 20);
  assert.equal(T.scrollPercent({ scrollLeft: 0, clientWidth: 9, scrollWidth: 9 }), 0);
});

test('mini-carte : traits et événements visibles', () => {
  const layout = { start: 60, slot: 100 };
  const view = { scrollLeft: 0, clientWidth: 500, scrollWidth: 1000 };
  assert.equal(T.markFraction(0, layout, view), 0.11);
  assert.equal(T.markFraction(8, layout, view), 0.91);
  assert.deepEqual(T.eventsInView(view, 9, layout), [1, 4], 'milieux visibles : 110, 210, 310, 410');
  assert.deepEqual(T.eventsInView({ ...view, scrollLeft: 500 }, 9, layout), [5, 9]);
  assert.equal(T.eventsInView(view, 0, layout), null);
  assert.equal(T.viewText([12, 24], 80), 'événements 12 à 24 sur 80');
  assert.equal(T.viewText([3, 3], 80), 'événement 3 sur 80');
  assert.equal(T.viewText(null, 0), '');
});
