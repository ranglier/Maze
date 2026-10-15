// session.js (lot 3 de l'audit, M3, M4, H3) : saisies en cours, texte non fusionné gardé dans le navigateur puis
// envoyé comme version nommée, avis par compte et par instance, effacement après KEEP_DAYS jours, rechargement
// quand le serveur change de version. Chaque test charge un exemplaire neuf du module sur un `window` neuf.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow, memoryStorage } from './helpers.mjs';

const LOST_KEY = 'maze:lost-edits';
const USER = { id: 3, username: 'mj', instance: 'abc123' };
let counter = 0;

/** Réponses de l'API simulées : (méthode, chemin, corps) -> { status, body } ; appels gardés dans `calls`. */
function fakeApi(handler) {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ method: options.method, path, body, headers: options.headers });
    const out = await handler(options.method, path, body);
    if (out instanceof Error) throw out;
    return { ok: out.status < 300, status: out.status, statusText: '', json: async () => out.body };
  };
  return calls;
}

async function fresh(t, { storage = {}, api = () => ({ status: 201, body: {} }) } = {}) {
  if (!t.timersMocked) {   // renvoi différé de 31 s, délais de 10 s : jamais attendus
    t.mock.timers.enable({ apis: ['setTimeout'] });
    t.timersMocked = true;
  }
  const win = fakeWindow(storage);
  const calls = fakeApi(api);
  counter += 1;
  const session = await import(`../src/session.js?exemplaire=${counter}`);
  return { session, win, calls };
}

const flush = () => new Promise((resolve) => { setImmediate(resolve); });
async function settle() {
  for (let i = 0; i < 20; i += 1) await flush();
}

const stored = (win) => JSON.parse(win.localStorage.getItem(LOST_KEY) || '[]');
const entry = (over = {}) => ({ id: `e${Math.random()}`, docId: 5, campaignId: 1, created: '2026-10-01T10:00:00Z',
  title: 'Bal', html: '<p>Texte</p>', reason: 'replaced', label: 'Modifications hors ligne de MJ',
  at: new Date().toISOString(), status: 'local', user: { id: USER.id, username: USER.username }, instance: USER.instance,
  ...over });

test('saisies en cours : confirmation avant de les quitter, avertissement à la fermeture', async (t) => {
  const { session, win } = await fresh(t);
  assert.equal(session.hasUnsaved(), false);
  assert.equal(await session.confirmDiscard(async () => { throw new Error('jamais demandé'); }), true);
  session.setUnsaved('npc-1', true, 'Vex');
  assert.equal(session.hasUnsaved(), true);
  let asked = null;
  assert.equal(await session.confirmDiscard(async (o) => { asked = o; return false; }), false);
  assert.match(asked.message, /« Vex »/);
  assert.equal(session.hasUnsaved(), true);
  const event = new Event('beforeunload', { cancelable: true });
  win.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(await session.confirmDiscard(async () => true), true);
  assert.equal(session.hasUnsaved(), false);
  const after = new Event('beforeunload', { cancelable: true });
  win.dispatchEvent(after);
  assert.equal(after.defaultPrevented, false);
});

test('texte abandonné : gardé dans le navigateur, envoyé comme version nommée sans en-tête de version', async (t) => {
  const { session, win, calls } = await fresh(t);
  session.setSessionUser(USER);
  await session.rescueEdits({ docId: 5, campaignId: 1, created: 'c', title: 'Bal', html: '<p>Ma phrase</p>',
    reason: 'replaced', author: 'ranglier' });
  const sent = calls.find((c) => c.method === 'POST');
  assert.equal(sent.path, '/api/documents/5/versions');
  assert.deepEqual(sent.body, { label: 'Modifications hors ligne de ranglier', content: '<p>Ma phrase</p>' });
  assert.equal(sent.headers['X-Maze-Version'], undefined);
  const [kept] = stored(win);
  assert.equal(kept.status, 'version');
  assert.equal(kept.html, '');                      // gardé côté serveur : plus besoin du texte ici
  assert.deepEqual(kept.user, { id: 3, username: 'mj' });
  assert.equal(kept.instance, 'abc123');
  assert.equal(session.lostEdits().length, 1);
});

test('envoi impossible : texte « local », montré à ce compte seulement, sur cette instance seulement', async (t) => {
  const { session, win } = await fresh(t, { api: () => new TypeError('réseau coupé') });
  session.setSessionUser(USER);
  await session.rescueEdits({ docId: 5, campaignId: 1, created: 'c', title: 'Bal', html: '<p>Hors ligne</p>',
    reason: 'closed', author: 'MJ' });
  assert.equal(stored(win)[0].status, 'local');
  assert.equal(stored(win)[0].html, '<p>Hors ligne</p>');
  assert.equal(session.localEdits().length, 1);
  session.setSessionUser(null);                    // écran de connexion : aucun avis
  assert.deepEqual(session.lostEdits(), []);
  session.setSessionUser({ ...USER, id: 4, username: 'autre' });
  assert.deepEqual(session.lostEdits(), []);
  session.setSessionUser({ ...USER, instance: 'recette' });
  assert.deepEqual(session.lostEdits(), []);
  assert.equal(stored(win).length, 1);             // toujours gardé pour son compte
  session.discardLocal();
  assert.equal(stored(win).length, 1);             // « Abandonner » : seulement les textes du compte connecté
});

test('reconnexion : texte renvoyé si la fiche est toujours la même, puis retiré du navigateur', async (t) => {
  const kept = entry();
  const storage = { localStorage: memoryStorage() };
  storage.localStorage.setItem(LOST_KEY, JSON.stringify([kept]));
  const { session, win, calls } = await fresh(t, {
    storage,
    api: (method, path) => (method === 'GET' ? { status: 200, body: { campaign_id: 1, created_at: kept.created } }
      : { status: 201, body: {} }),
  });
  session.setSessionUser(USER);
  await settle();
  assert.ok(calls.some((c) => c.method === 'POST' && c.path === '/api/documents/5/versions'));
  assert.deepEqual(stored(win), []);
  assert.deepEqual(session.lostEdits().map((e) => e.status), ['recovered']);
});

test('reconnexion : fiche supprimée (numéro repris ou 404) : rien d\'envoyé, texte gardé', async (t) => {
  const reused = entry({ id: 'repris' });
  const gone = entry({ id: 'disparu', docId: 6 });
  const storage = { localStorage: memoryStorage() };
  storage.localStorage.setItem(LOST_KEY, JSON.stringify([reused, gone]));
  const { session, win, calls } = await fresh(t, {
    storage,
    api: (method, path) => (path === '/api/documents/5' ? { status: 200, body: { campaign_id: 1, created_at: 'autre' } }
      : { status: 404, body: { detail: 'Fiche introuvable' } }),
  });
  session.setSessionUser(USER);
  await settle();
  assert.ok(!calls.some((c) => c.method === 'POST'));
  const byId = Object.fromEntries(stored(win).map((e) => [e.id, e]));
  assert.equal(byId.repris.error, 'la fiche a été supprimée');
  assert.match(byId.disparu.error, /supprimée, ou votre accès retiré/);
  assert.equal(session.localEdits().length, 2);
});

test('effacement après KEEP_DAYS jours, au chargement', async (t) => {
  const old = entry({ id: 'vieux', at: new Date(Date.now() - (8 * 24 * 3600 * 1000)).toISOString() });
  const recent = entry({ id: 'recent', status: 'version', html: '' });
  const storage = { localStorage: memoryStorage() };
  storage.localStorage.setItem(LOST_KEY, JSON.stringify([old, recent]));
  const { session, win } = await fresh(t, { storage });
  assert.deepEqual(stored(win).map((e) => e.id), ['recent']);
  assert.equal(session.KEEP_DAYS, 7);
  assert.equal(session.purgeDate(recent).getTime(), Date.parse(recent.at) + 7 * 24 * 3600 * 1000);
});

test('stockage plein : sessionStorage, puis la page seule', async (t) => {
  const { session, win } = await fresh(t, {
    storage: { localStorage: memoryStorage({ broken: true }) }, api: () => new TypeError('coupé') });
  session.setSessionUser(USER);
  await session.rescueEdits({ docId: 5, campaignId: 1, created: 'c', title: 'Bal', html: '<p>A</p>', reason: 'x' });
  assert.equal(JSON.parse(win.sessionStorage.getItem(LOST_KEY)).length, 1);
  const both = await fresh(t, {
    storage: { localStorage: memoryStorage({ broken: true }), sessionStorage: memoryStorage({ broken: true }) },
    api: () => new TypeError('coupé') });
  both.session.setSessionUser(USER);
  await both.session.rescueEdits({ docId: 5, campaignId: 1, created: 'c', title: 'Bal', html: '<p>B</p>', reason: 'x' });
  const [only] = both.session.lostEdits();
  assert.equal(only.memoryOnly, true);
  assert.equal(only.html, '<p>B</p>');
});

test('autre version de Maze : rechargement, sauf saisie en cours ou rechargement de moins d\'une minute', async (t) => {
  const { session, win } = await fresh(t);
  session.setUnsaved('f', true, 'Vex');
  win.dispatchEvent(new CustomEvent('maze:stale-client', { detail: { version: 9 } }));
  await settle();
  assert.deepEqual(session.staleClient(), { version: 9 });
  assert.equal(win.location.reloads, 0);           // bandeau « Maze a été mis à jour » à la place
  const other = await fresh(t);
  other.win.dispatchEvent(new CustomEvent('maze:stale-client', { detail: { version: 9 } }));
  await settle();
  assert.equal(other.win.location.reloads, 1);
  assert.equal(other.session.reloadForUpdate(), false);   // pas de boucle
  assert.equal(other.session.reloadForUpdate(true), true);
  assert.equal(other.win.location.reloads, 2);
});
