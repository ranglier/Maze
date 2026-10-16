// Parcours navigateur de la licence (AGPL-3.0-or-later, section 13) : le lien « Code source (AGPL-3.0) » est offert à
// tout utilisateur de l'instance, à l'adresse donnée par le serveur (MAZE_SOURCE_URL, /api/auth/status).
//   1. Écran de premier lancement puis écran de connexion : lien en pied de formulaire, avant toute connexion.
//   2. Menu du compte, une fois connecté : élément « Code source (AGPL-3.0) », qui ouvre l'adresse dans un nouvel
//      onglet sans lien vers l'onglet de Maze (rel="noopener noreferrer" : window.opener nul).
//   3. Adresse d'un fork (réponse de /api/auth/status modifiée) : l'interface suit l'adresse du serveur, aucune
//      adresse écrite en dur.
// Les pages externes sont simulées (aucune sortie vers Internet).
//
// Base vide ou déjà initialisée. Usage : node code-source.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const candidates = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean),
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
const LABEL = 'Code source (AGPL-3.0)';
const FORK = 'https://git.example.org/fork/maze';
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };

/** Lien vers le code source : adresse attendue, nouvel onglet, rel="noopener noreferrer". */
async function checkLink(link, url, where) {
  await link.waitFor({ timeout: 15000 });
  check(await link.isVisible(), `${where} : lien « ${LABEL} » visible`);
  const [href, target, rel] = await Promise.all(['href', 'target', 'rel'].map((a) => link.getAttribute(a)));
  check(href === url, `${where} : adresse ${href}`);
  const rels = (rel || '').split(/\s+/);
  check(target === '_blank' && rels.includes('noopener') && rels.includes('noreferrer'),
    `${where} : nouvel onglet, rel="${rel}"`);
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const status = await (await fetch(`${BASE}/api/auth/status`)).json();
  check(status.license === 'AGPL-3.0-or-later', `licence annoncée par /api/auth/status : ${status.license}`);
  const SOURCE = status.source_url;
  check(/^https?:\/\//.test(SOURCE || ''), `adresse du code source annoncée sans connexion : ${SOURCE}`);

  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, locale: 'fr-FR' });
  // Pages externes simulées : le nouvel onglet s'ouvre sans dépendre d'Internet.
  await ctx.route((url) => !url.href.startsWith(BASE), (route) => route.fulfill({
    status: 200, contentType: 'text/html', body: '<!doctype html><title>Sources</title><p>Sources</p>' }));
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(BASE + '/');
  await page.locator('#setup-username, #login-username').first().waitFor({ timeout: 15000 });

  // --- 1. Avant connexion : premier lancement ou écran de connexion -------------------------------------------
  if (await page.locator('#setup-username').isVisible()) {
    await checkLink(page.locator('.splash__footer a'), SOURCE, 'premier lancement');
    step(`Création du premier administrateur « ${USER} »`);
    await page.fill('#setup-username', USER);
    await page.fill('#setup-name', 'Admin E2E');
    await page.fill('#setup-password', PASSWORD);
    await page.fill('#setup-confirm', PASSWORD);
    await page.getByRole('button', { name: 'Créer le compte et entrer' }).click();
    await page.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
  } else {
    await checkLink(page.locator('.splash__footer a'), SOURCE, 'écran de connexion');
    await page.fill('#login-username', USER);
    await page.fill('#login-password', PASSWORD);
    await page.locator('form button[type=submit]').click();
    await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  }

  // --- 2. Menu du compte : élément du menu, nouvel onglet sans window.opener ----------------------------------
  await page.locator('.user-menu__trigger').click();
  const item = page.getByRole('menuitem', { name: LABEL });
  await checkLink(item, SOURCE, 'menu du compte');
  await page.screenshot({ path: path.join(SHOTS, 'code-source-menu.png') });
  const [tab] = await Promise.all([ctx.waitForEvent('page'), item.click()]);
  await tab.waitForLoadState('domcontentloaded');
  check(tab.url().replace(/\/$/, '') === SOURCE.replace(/\/$/, ''), `nouvel onglet ouvert sur ${tab.url()}`);
  check(await tab.evaluate(() => window.opener === null), 'nouvel onglet sans accès à Maze (window.opener nul)');
  await tab.close();
  check(await page.locator('.user-menu .popover').count() === 0, 'menu du compte refermé après le clic');

  // Déconnexion : lien de nouveau en pied de l'écran de connexion.
  await page.locator('.user-menu__trigger').click();
  await page.getByRole('menuitem', { name: 'Se déconnecter' }).click();
  await page.locator('#login-username').waitFor({ timeout: 15000 });
  await checkLink(page.locator('.splash__footer a'), SOURCE, 'écran de connexion après déconnexion');
  await page.screenshot({ path: path.join(SHOTS, 'code-source-connexion.png') });

  // --- 3. Fork : l'interface prend l'adresse donnée par le serveur --------------------------------------------
  await page.route('**/api/auth/status', async (route) => {
    const response = await route.fetch();
    route.fulfill({ response, json: { ...(await response.json()), source_url: FORK } });
  });
  await page.reload();
  await checkLink(page.locator('.splash__footer a'), FORK, 'adresse d’un fork (MAZE_SOURCE_URL)');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message.split('\n')[0]);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `code-source-echec-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `CODE SOURCE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'CODE SOURCE ÉCHEC');
process.exit(ok && !errors.length ? 0 : 1);
