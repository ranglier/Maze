// Parcours navigateur du lot 3 de l'audit, H3 (DATA-2) : un onglet resté sur une ancienne version de l'interface
// ne doit pas détruire de contenu chez les autres MJ.
//
// y-prosemirror supprime du document partagé les blocs que le schéma du navigateur ne connaît pas
// (createNodeFromYElement : schema.node lève une exception, le catch supprime l'élément Yjs ; live.js envoie
// cette suppression au serveur). Le parcours construit une interface « ancienne » : le code de ce dépôt dont le
// schéma de l'éditeur a perdu l'encadré (nœud callout) et dont la version de schéma est la précédente. Le
// navigateur A (interface actuelle) ouvre une fiche qui contient un encadré « Secret » ; le navigateur B charge
// l'ancienne interface (substituée au paquet JS servi, une seule fois) et ouvre la même fiche.
//   Avant la correction : B se connecte et l'encadré disparaît chez A et sur le serveur.
//   Après : B est refusé (code 4426), recharge la page de lui-même, obtient l'interface actuelle et voit l'encadré.
//
// Base vide ou déjà initialisée. Usage : node edition-version.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire),
//             MAZE_NODE_PATHS (dossiers node_modules où trouver esbuild et les dépendances de l'interface ;
//             à défaut NODE_PATH et frontend/node_modules).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const nodeDirs = [...(process.env.MAZE_NODE_PATHS || '').split(':'), ...(process.env.NODE_PATH || '').split(':'),
  path.join(FRONTEND, 'node_modules'), path.join(process.env.HOME || '/', '.npm-global/lib/node_modules'), '/opt/npm-tools/node_modules']
  .filter(Boolean);
const load = (name) => {
  for (const dir of nodeDirs) {
    try { return require(path.join(dir, name)); } catch { /* suivant */ }
  }
  throw new Error(`Module ${name} introuvable (MAZE_NODE_PATHS, NODE_PATH ?)`);
};
const { chromium } = load('playwright');
const esbuild = load('esbuild');

const BASE = (process.argv[2] || process.env.MAZE_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const USER = process.env.MAZE_E2E_USER || 'admin';
const PASSWORD = process.env.MAZE_E2E_PASSWORD || `e2e-${randomBytes(9).toString('base64url')}`;
const SHOTS = process.env.MAZE_E2E_SHOTS || path.join(tmpdir(), 'maze-e2e-shots');
const SECRET = `SECRET-H3-${randomBytes(3).toString('hex')}`;
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const sleep = (ms) => new Promise((r) => { setTimeout(r, ms); });

function watch(page, label) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e.message}`));
  page.on('websocket', (ws) => {
    step(`${label} WebSocket ${ws.url().replace(/^.*\/api/, '/api')}`);
    ws.on('framereceived', (f) => {
      if (typeof f.payload === 'string' && f.payload.includes('"error"')) step(`${label} reçu : ${f.payload}`);
    });
  });
}

/** Interface « ancienne » : schéma de l'éditeur sans l'encadré, version de schéma précédente. */
async function staleBundle() {
  const work = mkdtempSync(path.join(tmpdir(), 'maze-ancienne-'));
  try {
    cpSync(path.join(FRONTEND, 'src'), path.join(work, 'src'), { recursive: true });
    const schemaFile = path.join(work, 'src/editor/schema.js');
    const source = readFileSync(schemaFile, 'utf8');
    const start = source.indexOf('    callout: {');
    const end = source.indexOf('    bullet_list: {');
    if (start < 0 || end < start) throw new Error('Nœud callout introuvable dans schema.js');
    writeFileSync(schemaFile, source.slice(0, start) + source.slice(end));
    const versionFile = path.join(work, 'src/editor/schema-version.json');
    const current = existsSync(versionFile) ? JSON.parse(readFileSync(versionFile, 'utf8')).version : 1;
    const result = await esbuild.build({
      absWorkingDir: work, entryPoints: [path.join(work, 'src/main.jsx')], bundle: true, write: false,
      outdir: path.join(work, 'dist'), format: 'esm', minify: true, jsx: 'automatic', target: ['es2020'],
      nodePaths: nodeDirs, logLevel: 'error',
      define: { 'process.env.NODE_ENV': '"production"', __MAZE_SCHEMA_VERSION__: JSON.stringify(current - 1) },
    });
    const js = result.outputFiles.find((f) => f.path.endsWith('.js'));
    step(`Interface ancienne construite (schéma sans encadré, version ${current - 1}, ${Math.round(js.contents.length / 1024)} Ko)`);
    return js.text;
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
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

/** Ouvre l'adresse dans un chargement complet de la page (un simple changement de # ne recharge rien). */
async function open(page, url) {
  await page.goto(url);
  await page.reload();
}

const calloutText = (page) => page.evaluate(() => [...document.querySelectorAll('.ProseMirror [data-callout="secret"]')]
  .map((e) => e.textContent).join(' | '));

const bundle = await staleBundle();
const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctxA = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctxA.newPage();
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
  const campaign = await (await a.request.post(BASE + '/api/campaigns', { data: { name: `Versions ${SECRET}` } })).json();
  const doc = await (await a.request.post(`${BASE}/api/campaigns/${campaign.id}/documents`, { data: {
    title: 'Scène avec un secret',
    content: `<p>Avant l’encadré.</p><div class="callout" data-callout="secret"><p>${SECRET} : à garder</p></div><p>Après.</p>`,
  } })).json();
  const docUrl = `${BASE}/#/c/${campaign.id}/scenarios/${doc.id}`;

  // A : interface actuelle, la fiche passe dans le journal temps réel (conversion du HTML).
  await open(a, docUrl);
  await a.locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  await a.waitForFunction((s) => document.querySelector('.ProseMirror [data-callout="secret"]')?.textContent.includes(s),
    SECRET, { timeout: 10000 });
  step('A voit l’encadré « Secret »');

  // B : connecté avec l'interface actuelle, puis la page est rechargée avec l'ancienne (une seule fois, comme un
  // onglet ouvert avant une mise à jour).
  const ctxB = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const b = await ctxB.newPage();
  watch(b, 'B');
  await login(b, USER, PASSWORD);
  let served = 0;
  await b.route(/\/assets\/main-[^/]*\.js$/, async (route) => {
    if (served) return route.continue();
    served += 1;
    step('B reçoit l’interface ancienne');
    return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: bundle });
  });
  let loads = 0;
  await b.goto(docUrl);
  b.on('load', () => { loads += 1; });
  await b.reload();
  await sleep(6000);
  await a.screenshot({ path: path.join(SHOTS, 'h3-a.png') });
  await b.screenshot({ path: path.join(SHOTS, 'h3-b.png') });

  check((await calloutText(a)).includes(SECRET), 'l’encadré « Secret » est toujours là chez A');
  const saved = await (await a.request.get(`${BASE}/api/documents/${doc.id}`)).json();
  check(saved.content.includes(SECRET), 'l’encadré est toujours dans la fiche enregistrée');
  check(served === 1 && loads >= 2, `B a rechargé la page de lui-même (${loads} chargements depuis l’interface ancienne)`);
  await b.locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  await b.waitForFunction((s) => document.querySelector('.ProseMirror [data-callout="secret"]')?.textContent.includes(s),
    SECRET, { timeout: 10000 });
  step('B, rechargé avec l’interface actuelle, voit l’encadré et édite normalement');
  // Toujours là chez A après que B a rejoint la fiche.
  await sleep(1500);
  check((await calloutText(a)).includes(SECRET), 'l’encadré est toujours là chez A après le rechargement de B');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `h3-echec-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `H3 OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})` : 'H3 ÉCHEC');
process.exit(ok ? 0 : 1);
