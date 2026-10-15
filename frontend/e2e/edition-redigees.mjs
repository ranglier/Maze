// Parcours navigateur du lot 3 de l'audit, fiches rédigées :
//   M4 (DATA-7 = FRONT-2) : texte tapé hors ligne, alors que la fiche est remplacée entre-temps (ici par l'API, comme
//     update_document de Claude ou une restauration de version). À la reconnexion, le journal a changé d'époque et
//     les modifications ne peuvent plus être fusionnées : le texte local doit être gardé comme version nommée
//     (« Modifications hors ligne de <nom> ») et un avis doit rester affiché, fiche encore ouverte comme fiche
//     quittée pendant la coupure. Avant la correction : texte jeté sans trace (un toast de 3 s, ou rien).
//   FRONT-6 : statut d'une fiche dont l'enregistrement échoue pendant qu'on passe à une autre fiche : il ne doit pas
//     être appliqué à l'autre fiche (avant : envoyé avec le titre de la seconde, sous son identifiant).
// La coupure réseau est simulée sur le WebSocket (page.routeWebSocket) : connexion fermée, nouvelles tentatives
// refusées tant que la coupure dure ; le reste du réseau fonctionne.
//
// Base vide ou déjà initialisée. Usage : node edition-redigees.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const sleep = (ms) => new Promise((r) => { setTimeout(r, ms); });

function watch(page, label) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e.message}`));
}

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

/** Coupure réseau du temps réel : toutes les connexions WebSocket de la page passent par ici. */
async function networkSwitch(page) {
  const open = new Set();
  const state = { offline: false };
  await page.routeWebSocket(/\/api\/documents\/\d+\/live/, (ws) => {
    if (state.offline) {
      ws.close({ code: 4000, reason: 'hors ligne' });
      return;
    }
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

async function versionsOf(page, docId) {
  const { versions } = await api(page, 'GET', `/api/documents/${docId}/versions`);
  const out = [];
  for (const v of versions) out.push({ ...v, content: (await api(page, 'GET', `/api/documents/${docId}/versions/${v.id}`)).content });
  return out;
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
    await a.fill('#login-username', USER);
    await a.fill('#login-password', PASSWORD);
    await a.locator('form button[type=submit]').click();
    await a.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  }
  const me = (await api(a, 'GET', '/api/auth/status')).user;
  const author = me.display_name || me.username;
  const campaign = await api(a, 'POST', '/api/campaigns', { name: `Rédigées ${TAG}` });
  const mk = (title) => api(a, 'POST', `/api/campaigns/${campaign.id}/documents`, { title, content: `<p>Base de ${title}</p>` });
  const first = await mk('Le train');
  const other = await mk('Autre fiche');
  const third = await mk('Le quai');
  const net = await networkSwitch(a);
  const editor = a.locator('.ProseMirror[contenteditable=true]').first();

  const openDoc = async (doc) => {
    await a.goto(`${BASE}/#/c/${campaign.id}/scenarios/${doc.id}`);
    await editor.waitFor({ timeout: 15000 });
    await a.locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  };
  await a.goto(`${BASE}/#/c/${campaign.id}/scenarios/${first.id}`);
  await a.reload();
  await openDoc(first);

  // Chaque partie est vérifiée même si une précédente échoue (le parcours dit tout ce qui ne va pas).
  const failures = [];
  const part = async (name, fn) => {
    try {
      await fn();
    } catch (e) {
      failures.push(`${name} : ${e.message.split('\n')[0]}`);
      console.error(`ÉCHEC ${name} :`, e.message.split('\n')[0]);
      await a.screenshot({ path: path.join(SHOTS, `redigees-echec-${Date.now()}.png`) }).catch(() => {});
      net.on();
      await a.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
      await a.goto(`${BASE}/#/c/${campaign.id}/scenarios`);
      await a.reload();
    }
  };

  // --- M4, fiche restée ouverte ---------------------------------------------------------------------------
  await part('M4 fiche ouverte', async () => {
  const OFFLINE1 = `Écrit dans le train ${TAG}`;
  await net.off();
  step('Coupure du temps réel');
  await editor.click();
  await a.keyboard.press('Control+End');
  await a.keyboard.press('Enter');
  await a.keyboard.type(OFFLINE1, { delay: 10 });
  await sleep(500);
  // Pendant la coupure : la fiche est remplacée (époque du journal changée).
  await api(a, 'PATCH', `/api/documents/${first.id}`, { content: '<p>Remplacée par Claude</p>' });
  step('Fiche remplacée par l’API pendant la coupure');
  net.on();
  await a.locator('[data-lost-edits="version"]').first().waitFor({ timeout: 30000 });
  step('Avis des modifications non fusionnées affiché');
  await a.screenshot({ path: path.join(SHOTS, 'm4-avis.png') });
  await a.waitForFunction(() => document.querySelector('.ProseMirror')?.textContent.includes('Remplacée par Claude'),
    null, { timeout: 15000 });
  const versions = await versionsOf(a, first.id);
  const kept = versions.find((v) => v.label === `Modifications hors ligne de ${author}`);
  check(kept && kept.content.includes(OFFLINE1), 'texte tapé hors ligne gardé comme version nommée de la fiche');
  check(kept.kind === 'manual', 'version nommée (jamais élaguée)');
  await sleep(6000);
  check(await a.locator('[data-lost-edits="version"]').count() === 1, 'avis toujours affiché après 6 s');
  await a.reload();
  await editor.waitFor({ timeout: 15000 });
  check(await a.locator('[data-lost-edits]').count() === 1, 'avis toujours affiché après un rechargement');
  await a.getByRole('button', { name: 'J’ai compris' }).click();
  check(await a.locator('[data-lost-edits]').count() === 0, 'avis fermé par « J’ai compris »');
  });

  // --- M4, fiche quittée pendant la coupure --------------------------------------------------------------
  await part('M4 fiche quittée', async () => {
  await openDoc(third);
  const OFFLINE2 = `Écrit sur le quai ${TAG}`;
  await net.off();
  await editor.click();
  await a.keyboard.press('Control+End');
  await a.keyboard.press('Enter');
  await a.keyboard.type(OFFLINE2, { delay: 10 });
  await sleep(300);
  await a.locator('.doc-tree .doc-tree__link', { hasText: 'Autre fiche' }).first().click();
  await a.waitForURL(new RegExp(`/scenarios/${other.id}$`), { timeout: 10000 });
  step('Fiche quittée pendant la coupure (modifications en attente d’envoi)');
  await api(a, 'PATCH', `/api/documents/${third.id}`, { content: '<p>Le quai, réécrit</p>' });
  net.on();
  await a.locator('[data-lost-edits="version"]').first().waitFor({ timeout: 30000 });
  const versions = await versionsOf(a, third.id);
  check(versions.some((v) => v.label.startsWith('Modifications hors ligne') && v.content.includes(OFFLINE2)),
    'fiche quittée : texte tapé hors ligne gardé comme version nommée, avis affiché sur l’autre fiche');
  await a.getByRole('button', { name: 'J’ai compris' }).click();
  });

  // --- FRONT-6 : statut d'une fiche après un échec d'enregistrement -----------------------------------------
  await part('FRONT-6', async () => {
  await openDoc(first);
  let failed = 0;
  const sent = [];
  await a.route(new RegExp(`/api/documents/(${first.id}|${other.id})$`), async (route) => {
    const req = route.request();
    if (req.method() === 'PATCH') sent.push({ url: req.url(), body: req.postDataJSON() });
    if (req.method() === 'PATCH' && req.url().endsWith(`/${first.id}`) && !failed && req.postDataJSON()?.status) {
      failed += 1;
      await sleep(1500);
      return route.fulfill({ status: 502, contentType: 'application/json', body: '{"detail":"Passerelle indisponible"}' });
    }
    return route.continue();
  });
  await a.locator('.status-pick__opt.status--joue').click();
  await a.locator('.doc-tree .doc-tree__link', { hasText: 'Autre fiche' }).first().click();
  await a.waitForURL(new RegExp(`/scenarios/${other.id}$`), { timeout: 10000 });
  await editor.waitFor({ timeout: 15000 });
  await sleep(1800);   // l'enregistrement du statut de la première fiche a échoué entre-temps
  const title = a.getByRole('textbox', { name: 'Titre de la fiche' });
  await title.click();
  await a.keyboard.press('End');
  await a.keyboard.type(' bis', { delay: 20 });
  await sleep(2500);
  const second = await api(a, 'GET', `/api/documents/${other.id}`);
  step(`Requêtes : ${JSON.stringify(sent.map((s) => [s.url.replace(/^.*\/documents\//, ''), s.body]))}`);
  check(second.title === 'Autre fiche bis', 'titre de la seconde fiche enregistré');
  check(second.status === 'brouillon', 'statut de la première fiche jamais appliqué à la seconde');
  check(!sent.some((s) => s.url.endsWith(`/${other.id}`) && s.body?.status), 'aucun statut envoyé pour la seconde fiche');
  const firstNow = await api(a, 'GET', `/api/documents/${first.id}`);
  check(firstNow.status === 'joue', 'statut de la première fiche renvoyé pour elle seule');
  });
  if (failures.length) throw new Error(failures.join(' ; '));
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `redigees-echec-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `FICHES RÉDIGÉES OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'FICHES RÉDIGÉES ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
