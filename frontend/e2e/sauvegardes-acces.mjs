// Parcours navigateur du lot 1 de l'audit (Playwright, Chromium headless) : jetons Foundry révoqués avec leur
// créateur (M12, Administration > Foundry VTT), origine des sauvegardes (M8), restauration qui ferme les autres
// sessions mais garde celle de l'administrateur qui restaure, et compte supprimé depuis l'archive restauré
// désactivé et annoncé (M11).
//
// Base vide ou déjà initialisée. Usage : node sauvegardes-acces.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (dossier des captures, défaut : maze-e2e-shots dans le dossier temporaire).
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
const ADMIN2 = `admin2-${TAG}`;
const MJ = `mj-${TAG}`;
const OTHER_PW = `autre-${randomBytes(6).toString('hex')}`;
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };

function watch(page, label) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`${label} console: ${m.text()}`); });
}

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

async function login(page, username, password) {
  await page.goto(BASE + '/');
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

async function foundryStatus(token) {
  const res = await fetch(`${BASE}/api/foundry/v1/campaigns`, { headers: { Authorization: `Bearer ${token}` } });
  return res.status;
}

async function adminSection(page, key, title) {
  await page.goto(`${BASE}/#/admin/${key}`);
  await page.getByRole('heading', { name: title, exact: true }).first().waitFor({ timeout: 15000 });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctxA = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctxA.newPage();
  watch(a, 'A');
  await a.goto(BASE + '/');
  // Premier lancement ou connexion : attendre que l'un des deux formulaires soit affiché (isVisible n'attend pas).
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
  const camp = await api(a, 'POST', '/api/campaigns', { name: `Sauvegardes ${TAG}` });
  const admin2 = await api(a, 'POST', '/api/admin/users', { username: ADMIN2, role: 'admin', password: OTHER_PW });
  await api(a, 'POST', '/api/admin/users', { username: MJ, role: 'mj', password: OTHER_PW, campaign_ids: [camp.id] });
  step(`Campagne ${camp.id}, comptes ${ADMIN2} (administrateur) et ${MJ} (MJ)`);

  // 1. admin2 crée un jeton Foundry depuis l'interface.
  const ctxB = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const b = await ctxB.newPage();
  watch(b, 'B');
  await login(b, ADMIN2, OTHER_PW);
  await adminSection(b, 'foundry', 'Foundry VTT');
  await b.getByRole('button', { name: 'Nouveau jeton' }).click();
  const tokenName = `Monde ${TAG}`;
  await b.fill('#foundry-token-name', tokenName);
  await b.locator('.modal label.check', { hasText: camp.name }).locator('input').check();
  await b.getByRole('button', { name: 'Créer le jeton' }).click();
  const token = await b.locator('#foundry-new-token').inputValue({ timeout: 10000 });
  await b.getByRole('button', { name: 'J’ai copié le jeton' }).click();
  check(token.startsWith('mzf_') && (await foundryStatus(token)) === 200, 'jeton de admin2 créé et accepté par l’API Foundry');

  // 2. L'administrateur voit le jeton, rétrograde admin2 dans Comptes, puis la liste Foundry ne le montre plus.
  await adminSection(a, 'foundry', 'Foundry VTT');
  const row = a.locator('table tr', { hasText: tokenName });
  await row.waitFor({ timeout: 10000 });
  check((await row.textContent()).includes(ADMIN2), 'Foundry VTT : jeton listé avec son créateur');
  await adminSection(a, 'comptes', 'Comptes');
  await a.locator('table tr', { hasText: ADMIN2 }).getByRole('button', { name: 'Modifier' }).click();
  await a.locator('.modal [role=radio]', { hasText: 'MJ' }).click();
  await a.locator('.modal button[type=submit][form=user-form]').click();
  await a.locator('.modal').waitFor({ state: 'detached', timeout: 10000 });
  await a.locator('table tr', { hasText: ADMIN2 }).locator('.role-chip--mj').waitFor({ timeout: 10000 });
  step(`${ADMIN2} rétrogradé en MJ depuis Administration > Comptes`);
  await adminSection(a, 'foundry', 'Foundry VTT');
  await a.locator('.admin__block').nth(1).waitFor();
  await a.waitForTimeout(300);
  check(await a.locator('table tr', { hasText: tokenName }).count() === 0, 'Foundry VTT : jeton retiré de la liste');
  check((await foundryStatus(token)) === 401, 'jeton refusé par l’API Foundry (401)');
  await a.screenshot({ path: path.join(SHOTS, 'lot1-foundry.png') });
  await api(a, 'PATCH', `/api/admin/users/${admin2.id}`, { role: 'admin' });

  // 3. Sauvegarde manuelle : origine « Manuelle ».
  await adminSection(a, 'sauvegardes', 'Sauvegardes');
  await a.getByRole('button', { name: 'Sauvegarder maintenant' }).click();
  const manual = a.locator('table tr', { hasText: '-manuelle.tar.gz' }).first();
  await manual.waitFor({ timeout: 15000 });
  check((await manual.textContent()).includes('Manuelle'), 'Sauvegardes : origine « Manuelle »');
  const archive = (await manual.locator('.table__mono').textContent()).trim();
  const usage = a.locator('p', { hasText: 'libres sur le volume' });
  await usage.waitFor({ timeout: 10000 });
  check(/\d+ archives?, .+ au total · .+ libres sur le volume/.test(await usage.textContent()), 'Sauvegardes : place des archives et espace libre affichés');
  check((await a.locator('.admin__head p').first().textContent()).includes('MAZE_BACKUP_KEEP'), 'Sauvegardes : quotas affichés');
  const daily = (await usage.textContent()).includes('dernière sauvegarde quotidienne')
    || (await a.locator('.notice--warning', { hasText: 'sauvegarde quotidienne' }).count()) > 0;
  check(daily, 'Sauvegardes : dernière sauvegarde quotidienne affichée, ou absence signalée');

  // 4. Un MJ est connecté et admin2 (présent dans l'archive) est supprimé ; l'administrateur restaure : la session
  // du MJ est fermée, pas la sienne ; admin2 revient désactivé, et l'interface le dit.
  await api(a, 'DELETE', `/api/admin/users/${admin2.id}`);
  const ctxC = await browser.newContext({ viewport: { width: 1200, height: 800 }, locale: 'fr-FR' });
  const c = await ctxC.newPage();
  watch(c, 'C');
  await login(c, MJ, OTHER_PW);
  check((await (await c.request.get(BASE + '/api/auth/status')).json()).authenticated, `${MJ} connecté`);
  await manual.getByRole('button', { name: 'Restaurer' }).click();
  const dialog = a.locator('.modal[role=dialog]');
  check((await dialog.textContent()).includes('Toutes les sessions seront fermées'), 'confirmation : sessions fermées annoncées');
  await dialog.getByRole('button', { name: 'Restaurer' }).click();
  const done = a.locator('.modal[role=dialog]', { hasText: 'Sauvegarde restaurée' });
  await done.waitFor({ timeout: 30000 });
  step(`Restauration de ${archive}`);
  const doneText = await done.textContent();
  check(doneText.includes('restaurés désactivés') && doneText.includes(ADMIN2), `message : ${ADMIN2} restauré désactivé`);
  // L'interface se recharge : attente posée avant le clic (un rechargement rapide finirait avant elle).
  await Promise.all([a.waitForEvent('load', { timeout: 15000 }), done.getByRole('button', { name: 'Recharger' }).click()]);
  await a.getByRole('heading', { name: 'Sauvegardes', exact: true }).first().waitFor({ timeout: 15000 });
  check(!(await a.locator('#login-username').isVisible()), 'l’administrateur qui restaure reste connecté');
  check((await a.locator('table tr', { hasText: '-avant-restauration.tar.gz' }).first().textContent()).includes('Avant restauration'),
    'Sauvegardes : archive « Avant restauration » listée');
  await adminSection(a, 'comptes', 'Comptes');
  await a.locator('table tr', { hasText: ADMIN2 }).locator('.role-chip--off').waitFor({ timeout: 10000 });
  check(true, `Comptes : ${ADMIN2} affiché « Désactivé »`);
  await c.reload();
  await c.locator('#login-username').waitFor({ timeout: 15000 });
  check(true, `${MJ} renvoyé à l’écran de connexion`);
  await login(c, MJ, OTHER_PW);
  check((await (await c.request.get(BASE + '/api/auth/status')).json()).authenticated, `${MJ} se reconnecte avec son mot de passe`);
  await a.screenshot({ path: path.join(SHOTS, 'lot1-sauvegardes.png') });
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const p of browser.contexts().flatMap((ctx) => ctx.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `echec-lot1-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
const relevant = errors.filter((e) => !/favicon|401|Unauthorized/.test(e));
if (relevant.length) { console.log('Erreurs navigateur :'); relevant.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `LOT 1 OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})` : 'LOT 1 ÉCHEC');
process.exit(ok && !relevant.some((e) => /pageerror/.test(e)) ? 0 : 1);
