// Parcours navigateur du lot 3 de l'audit (contre-revue de M4) : texte non fusionné gardé dans le navigateur.
//   - Deux onglets du même navigateur gardent chacun un texte en même temps : aucun n'efface l'autre dans le
//     stockage (maze:lost-edits relu avant chaque modification, événement storage) et chaque onglet affiche les deux
//     avis.
//   - Poste partagé : un texte gardé dans le navigateur porte le compte et l'instance ; l'avis ne s'affiche ni sur
//     l'écran de connexion ni à un autre compte ; à la reconnexion du bon compte, le texte est de nouveau envoyé comme
//     version nommée et retiré du navigateur ; à la déconnexion, un avertissement propose de l'enregistrer, de le
//     télécharger ou de l'abandonner ; au-delà de 7 jours, il est effacé (date annoncée dans l'avis).
// L'enregistrement de la version est mis en échec (réponse 500 simulée) pour que le texte reste dans le navigateur.
//
// Base vide ou déjà initialisée. Usage : node edition-avis.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const candidates = [...(process.env.NODE_PATH || '').split(':').filter(Boolean),
  path.join(process.env.HOME || '/', '.npm-global/lib/node_modules'), '/opt/npm-tools/node_modules'];
let chromium;
for (const dir of candidates) {
  try { ({ chromium } = require(path.join(dir, 'playwright'))); break; } catch { /* suivant */ }
}
if (!chromium) throw new Error('Module playwright introuvable (NODE_PATH ?)');

const BASE = (process.argv[2] || process.env.MAZE_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const USER = process.env.MAZE_E2E_USER || 'admin';
const PASSWORD = process.env.MAZE_E2E_PASSWORD || `e2e-${randomBytes(9).toString('base64url')}`;
const SHOTS = process.env.MAZE_E2E_SHOTS || path.join(tmpdir(), 'maze-e2e-shots');
const TAG = randomBytes(3).toString('hex');
const OTHER = `mj-avis-${TAG}`;
const OTHER_PW = `autre-${randomBytes(6).toString('hex')}`;
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const sleep = (ms) => new Promise((r) => { setTimeout(r, ms); });
const DAY = 24 * 3600 * 1000;

function watch(page, label) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e.message}`));
}

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

async function networkSwitch(page) {
  const open = new Set();
  const state = { offline: false };
  await page.routeWebSocket(/\/api\/documents\/\d+\/live/, (ws) => {
    if (state.offline) { ws.close({ code: 4000, reason: 'hors ligne' }); return; }
    const server = ws.connectToServer();
    const pair = { ws, server };
    open.add(pair);
    ws.onClose(() => { open.delete(pair); server.close().catch(() => {}); });
    server.onClose(() => { open.delete(pair); ws.close().catch(() => {}); });
  });
  return {
    async off() {
      state.offline = true;
      for (const { ws, server } of [...open]) {
        await ws.close({ code: 4000, reason: 'coupure' }).catch(() => {});
        await server.close().catch(() => {});
      }
      open.clear();
    },
    on() { state.offline = false; },
  };
}

const stored = (page) => page.evaluate(() => {
  const raw = localStorage.getItem('maze:lost-edits') || sessionStorage.getItem('maze:lost-edits') || '[]';
  return JSON.parse(raw);
});

async function login(page, username, password) {
  await page.locator('#login-username').waitFor({ timeout: 15000 });
  await page.fill('#login-username', username);
  await page.fill('#login-password', password);
  const listed = page.waitForResponse((r) => r.url().endsWith('/api/campaigns') && r.request().method() === 'GET')
    .catch(() => null);
  await page.locator('form button[type=submit]').click();
  await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  // Liste des campagnes reçue : l'interface remplace alors l'adresse par celle de la campagne ; un page.goto('#/…')
  // lancé avant était parfois écrasé (onglet resté sur la fiche de campagne, intégration continue).
  await listed;
  await page.evaluate(() => new Promise((r) => { requestAnimationFrame(() => setTimeout(r, 50)); }));
}

async function logout(page) {
  await page.locator('.user-menu__trigger').click();
  await page.getByRole('menuitem', { name: 'Se déconnecter' }).click();
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctx.newPage();
  watch(a, 'A');
  await a.goto(BASE + '/');
  await a.locator('#setup-username, #login-username').first().waitFor({ timeout: 15000 });
  if (await a.locator('#setup-username').isVisible()) {
    step(`Création du premier administrateur « ${USER} »`);
    await a.fill('#setup-username', USER);
    await a.fill('#setup-name', 'Admin E2E');
    await a.fill('#setup-password', PASSWORD);
    await a.fill('#setup-confirm', PASSWORD);
    await a.getByRole('button', { name: 'Créer le compte et entrer' }).click();
    await a.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
  } else {
    await login(a, USER, PASSWORD);
  }
  const me = (await api(a, 'GET', '/api/auth/status')).user;
  const campaign = await api(a, 'POST', '/api/campaigns', { name: `Avis ${TAG}` });
  const mk = (title) => api(a, 'POST', `/api/campaigns/${campaign.id}/documents`, { title, content: `<p>Base de ${title}</p>` });
  const docs = [await mk('Premier onglet'), await mk('Second onglet')];
  await api(a, 'POST', '/api/admin/users', { username: OTHER, role: 'mj', password: OTHER_PW, campaign_ids: [campaign.id] });

  // Enregistrement des versions en panne : le texte reste dans le navigateur.
  let failing = true;
  let slow = false;   // réponse retardée : deux onglets envoient en même temps (contre-revue)
  await ctx.route(/\/api\/documents\/\d+\/versions$/, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    if (failing) return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Panne simulée"}' });
    if (slow) await sleep(1500);
    return route.continue();
  });

  // --- Deux onglets gardent chacun un texte au même moment -------------------------------------------------
  const b = await ctx.newPage();
  watch(b, 'B');
  const tabs = [a, b];
  const nets = [await networkSwitch(a), await networkSwitch(b)];
  const texts = [`Texte de l’onglet A ${TAG}`, `Texte de l’onglet B ${TAG}`];
  for (let i = 0; i < 2; i += 1) {
    await tabs[i].goto(`${BASE}/#/c/${campaign.id}/scenarios/${docs[i].id}`);
    await tabs[i].reload();
    await tabs[i].locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  }
  for (let i = 0; i < 2; i += 1) {
    await nets[i].off();
    const editor = tabs[i].locator('.ProseMirror[contenteditable=true]').first();
    await editor.click();
    await tabs[i].keyboard.press('Control+End');
    await tabs[i].keyboard.press('Enter');
    await tabs[i].keyboard.type(texts[i], { delay: 5 });
  }
  await sleep(400);
  for (const doc of docs) await api(a, 'PATCH', `/api/documents/${doc.id}`, { content: '<p>Remplacée</p>' });
  nets[0].on();
  nets[1].on();
  for (const tab of tabs) {
    await tab.waitForFunction(() => document.querySelectorAll('[data-lost-edits="local"]').length === 2, null,
      { timeout: 30000 });
  }
  await a.screenshot({ path: path.join(SHOTS, 'avis-deux-onglets.png') });
  let entries = await stored(a);
  check(entries.length === 2 && texts.every((t) => entries.some((e) => e.html.includes(t))),
    'deux onglets : les deux textes gardés dans le navigateur, et deux avis dans chaque onglet');
  check(entries.every((e) => e.user?.id === me.id && e.user?.username === me.username && /^[0-9a-f]{12}$/.test(e.instance)),
    'chaque texte gardé porte le compte et l’instance');
  await b.close();

  // --- Déconnexion avec du texte gardé : avertissement ----------------------------------------------------
  await logout(a);
  const ask = a.locator('[data-logout-lost]');
  await ask.waitFor({ timeout: 5000 });
  check((await ask.textContent()).includes('2'), 'déconnexion : avertissement des 2 textes gardés dans ce navigateur');
  await ask.getByRole('button', { name: 'Annuler' }).click();
  check(await a.locator('#login-username').count() === 0, 'toujours connecté après « Annuler »');

  // --- Écran de connexion et autre compte : aucun avis --------------------------------------------------------
  await ctx.clearCookies();   // session expirée
  await a.reload();
  await a.locator('#login-username').waitFor({ timeout: 15000 });
  await sleep(500);
  check(await a.locator('[data-lost-edits]').count() === 0, 'écran de connexion : aucun avis, aucun texte de campagne');
  await login(a, OTHER, OTHER_PW);
  await sleep(1000);
  check(await a.locator('[data-lost-edits]').count() === 0, 'autre compte : aucun avis');
  check((await stored(a)).length === 2, 'autre compte : les textes du premier restent intacts');
  await logout(a);
  await a.locator('#login-username').waitFor({ timeout: 10000 });

  // --- Reconnexion du bon compte : nouvel envoi, texte retiré du navigateur -------------------------------------
  failing = false;
  await login(a, USER, PASSWORD);
  await a.waitForFunction(() => {
    const raw = localStorage.getItem('maze:lost-edits') || '[]';
    return JSON.parse(raw).length === 0;
  }, null, { timeout: 20000 });
  for (let i = 0; i < 2; i += 1) {
    const { versions } = await api(a, 'GET', `/api/documents/${docs[i].id}/versions`);
    let found = false;
    for (const v of versions.filter((x) => x.label.startsWith('Modifications hors ligne'))) {
      if ((await api(a, 'GET', `/api/documents/${docs[i].id}/versions/${v.id}`)).content.includes(texts[i])) found = true;
    }
    check(found, `reconnexion : texte de l’onglet ${'AB'[i]} enregistré comme version nommée`);
  }
  check(await a.locator('[data-lost-edits="recovered"]').count() === 2, 'reconnexion : avis « enregistré dans l’historique »');
  check((await stored(a)).length === 0, 'reconnexion : plus aucun texte dans le navigateur');
  const understood = a.getByRole('button', { name: 'J’ai compris' });
  while (await understood.count()) await understood.first().click();

  // --- Nouvel envoi (contre-revue) : jamais deux fois, jamais vers une fiche qui a repris le numéro -------------
  const status = await api(a, 'GET', '/api/auth/status');
  const local = (doc, label) => ({ id: `local-${label}`, user: { id: me.id, username: me.username },
    instance: status.instance, docId: doc.id, campaignId: campaign.id, created: doc.created_at, title: doc.title,
    html: `<p>${label} ${TAG}</p>`, reason: 'reset', label: 'Modifications hors ligne', status: 'local',
    at: new Date().toISOString() });
  const copies = async (doc, text) => {
    let n = 0;
    const { versions } = await api(a, 'GET', `/api/documents/${doc.id}/versions`);
    for (const v of versions.filter((x) => x.label.startsWith('Modifications hors ligne'))) {
      if ((await api(a, 'GET', `/api/documents/${doc.id}/versions/${v.id}`)).content.includes(text)) n += 1;
    }
    return n;
  };
  // Deux onglets ouverts en même temps sur un texte gardé : un seul envoi.
  await a.evaluate((list) => localStorage.setItem('maze:lost-edits', JSON.stringify(list)), [local(docs[1], 'Deux onglets')]);
  const c = await ctx.newPage();
  watch(c, 'C');
  slow = true;
  await Promise.all([a.reload(), c.goto(`${BASE}/`)]);
  await a.waitForFunction(() => JSON.parse(localStorage.getItem('maze:lost-edits') || '[]').length === 0, null,
    { timeout: 20000 });
  await sleep(1500);
  await sleep(1500);
  slow = false;
  check(await copies(docs[1], `Deux onglets ${TAG}`) === 1, 'deux onglets à la connexion : une seule version envoyée');
  await c.close();
  for (const understood2 of [a.getByRole('button', { name: 'J’ai compris' })]) {
    while (await understood2.count()) await understood2.first().click();
  }
  // Fiche supprimée, numéro repris par une nouvelle fiche (identifiants réutilisés par SQLite) : rien n'y est envoyé.
  const gone = await mk('Supprimée ensuite');
  await a.evaluate((list) => localStorage.setItem('maze:lost-edits', JSON.stringify(list)), [local(gone, 'Numéro repris')]);
  await api(a, 'DELETE', `/api/documents/${gone.id}`);
  await sleep(1100);  // created_at à la seconde
  const reused = await mk('Nouvelle fiche');
  await a.reload();
  await a.locator('[data-lost-edits="local"]').first().waitFor({ timeout: 15000 });
  await sleep(1000);
  const left = await stored(a);
  check(reused.id !== gone.id || (await copies(reused, `Numéro repris ${TAG}`) === 0 && left.length === 1
    && /supprimée/.test(left[0].error || '')), `numéro ${gone.id} repris (${reused.id}) : texte gardé, rien envoyé à l’autre fiche`);
  await a.evaluate(() => localStorage.removeItem('maze:lost-edits'));

  // --- Purge au-delà de 7 jours, annoncée ---------------------------------------------------------------------
  const old = (days, label) => ({ id: `vieux-${label}`, user: { id: me.id, username: me.username }, instance: status.instance,
    docId: docs[0].id, campaignId: campaign.id, title: `Vieux ${label}`, html: `<p>Vieux ${label}</p>`, reason: 'reset',
    label: 'Modifications hors ligne', status: 'local', at: new Date(Date.now() - days * DAY).toISOString() });
  failing = true;
  await a.evaluate((list) => localStorage.setItem('maze:lost-edits', JSON.stringify(list)), [old(8, 'huit'), old(6.5, 'six')]);
  await a.reload();
  const kept = a.locator('[data-lost-edits="local"]');
  await kept.first().waitFor({ timeout: 15000 });
  entries = await stored(a);
  check(entries.length === 1 && entries[0].id === 'vieux-six', 'texte de plus de 7 jours effacé du navigateur');
  check((await kept.textContent()).includes('effacé de ce navigateur le'), 'avis : date d’effacement annoncée');
  check(await a.locator('[data-purge-soon]').count() === 1, 'avis : effacement dans moins d’un jour signalé');

  // --- Déconnexion : « Abandonner » efface le texte ---------------------------------------------------------
  await logout(a);
  await ask.waitFor({ timeout: 5000 });
  await ask.getByRole('button', { name: 'Abandonner et se déconnecter' }).click();
  await a.locator('#login-username').waitFor({ timeout: 10000 });
  check((await stored(a)).length === 0, 'déconnexion avec « Abandonner » : plus rien dans le navigateur');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message.split('\n')[0]);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `avis-echec-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `AVIS OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'AVIS ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
