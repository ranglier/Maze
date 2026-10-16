// Parcours navigateur de la langue de l'interface (src/i18n, migration 030) : ordre réglage du compte, choix de
// l'écran de connexion (ce navigateur), langue du navigateur, langue par défaut de l'instance (MAZE_DEFAULT_LOCALE).
//   1. Navigateur en anglais (en-US) : écran de connexion ou de premier lancement en anglais, <html lang="en">,
//      message d'erreur du serveur en anglais (en-tête Accept-Language).
//   2. Connecté : onglets, recherche et menu du compte en anglais ; Langue > Français : interface en français, gardé
//      par le compte (rechargement) ; Automatique : retour à la langue du navigateur.
//   3. Écran de connexion : petit sélecteur de langue, choix gardé dans le navigateur (rechargement), Automatique.
//   4. Navigateur en français, compte réglé sur English : interface en anglais ; le compte l'emporte.
// Seuls les fichiers transversaux sont traduits à ce stade (App, menu du compte, écran de connexion…) : le parcours
// ne vérifie qu'eux. Captures en anglais dans MAZE_E2E_SHOTS (langue-en-*.png).
//
// Base vide ou déjà initialisée. Usage : node langue.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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
const TAG = randomBytes(3).toString('hex');
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
const lang = (page) => page.evaluate(() => document.documentElement.lang);
const tabs = (page) => page.locator('.module-tabs a span').allTextContents();

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  return { status: res.status(), body: await res.json().catch(() => null) };
}

async function login(page, password = PASSWORD) {
  await page.fill('#login-username', USER);
  await page.fill('#login-password', password);
  await page.locator('form button[type=submit]').click();
}

async function openMenu(page) {
  await page.locator('.user-menu__trigger').click();
  await page.locator('[data-locale-menu]').waitFor({ timeout: 10000 });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const status = await (await fetch(`${BASE}/api/auth/status`)).json();
  check(status.default_locale === 'fr', `langue par défaut de l'instance : ${status.default_locale}`);

  // --- 1. Navigateur en anglais : écran avant connexion, message du serveur -------------------------------------
  const en = await browser.newContext({ viewport: { width: 1280, height: 820 }, locale: 'en-US' });
  const page = await en.newPage();
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(BASE + '/');
  await page.locator('#setup-username, #login-username').first().waitFor({ timeout: 15000 });
  check(await lang(page) === 'en', '<html lang="en"> pour un navigateur en anglais');
  if (await page.locator('#setup-username').isVisible()) {
    check(await page.getByText('First start: create the administrator account.').isVisible(), 'premier lancement en anglais');
    await shot(page, 'langue-en-premier-lancement');
    step(`Création du premier administrateur « ${USER} »`);
    await page.fill('#setup-username', USER);
    await page.fill('#setup-name', 'Admin E2E');
    await page.fill('#setup-password', PASSWORD);
    await page.fill('#setup-confirm', PASSWORD);
    await page.getByRole('button', { name: 'Create the account and enter' }).click();
    await page.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
    await openMenu(page);
    await page.getByRole('menuitem', { name: 'Sign out' }).click();
    await page.locator('#login-username').waitFor({ timeout: 15000 });
  }
  check(await page.getByRole('button', { name: 'Sign in' }).isVisible(), 'écran de connexion en anglais (« Sign in »)');
  check(await page.locator('label[for=login-username]').textContent() === 'Username', 'libellé « Username »');
  check(await page.getByText('The game master\'s workshop.').isVisible(), 'accroche en anglais');
  check(await page.locator('.splash__footer a').textContent() === 'Source code (AGPL-3.0)', 'lien « Source code (AGPL-3.0) »');
  await login(page, 'mauvais-mot-de-passe');
  const refusal = page.locator('.form-error[role=alert]');
  await refusal.waitFor({ timeout: 15000 });
  check(await refusal.textContent() === 'Incorrect username or password', `message du serveur en anglais : ${await refusal.textContent()}`);
  await shot(page, 'langue-en-connexion');

  // --- 2. Connecté : interface en anglais, menu du compte > Langue ------------------------------------------------
  await login(page);
  await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  const created = await api(page, 'POST', '/api/campaigns', { name: `Langue ${TAG}` });
  check(created.status === 201, 'campagne créée');
  await page.goto(`${BASE}/#/c/${created.body.id}`);
  await page.reload();
  await page.locator('.module-tabs').waitFor({ timeout: 15000 });
  check(JSON.stringify(await tabs(page)) === JSON.stringify(['Scenarios', 'Graph', 'Characters', 'Places']),
    `onglets en anglais : ${(await tabs(page)).join(', ')}`);
  check(await page.locator('.search-trigger__label').textContent() === 'Search', 'recherche : « Search »');
  check(await page.locator('.search-trigger').getAttribute('title')
    === 'Search NPCs, factions, places, scenarios and comments (Ctrl+K)', 'bulle de la recherche en anglais');
  await openMenu(page);
  const menu = page.locator('.user-menu .popover');
  check(await menu.getByRole('menuitem', { name: 'Change my password' }).isVisible(), 'menu du compte en anglais');
  check(await menu.getByText('Administrator').count() === 1, 'rôle « Administrator »');
  const choice = (name) => menu.getByRole('menuitemradio', { name, exact: true });
  check(await choice('Automatic').getAttribute('aria-checked') === 'true', 'langue du compte : Automatic');
  check(await choice('Français').isVisible() && await choice('English').isVisible(), 'choix Français et English');
  await shot(page, 'langue-en-menu');

  const saved = page.waitForResponse((r) => r.url().endsWith('/api/auth/locale') && r.request().method() === 'PUT');
  await choice('Français').click();
  check((await saved).status() === 200, 'langue du compte enregistrée (PUT /api/auth/locale)');
  await page.waitForFunction(() => document.querySelector('.module-tabs a span')?.textContent === 'Scénarios');
  check(JSON.stringify(await tabs(page)) === JSON.stringify(['Scénarios', 'Carte', 'Personnages', 'Lieux']),
    'onglets en français aussitôt');
  check(await lang(page) === 'fr', '<html lang="fr">');
  check(await menu.getByRole('menuitemradio', { name: 'Français' }).getAttribute('aria-checked') === 'true',
    'Français coché');
  await page.reload();
  await page.locator('.module-tabs').waitFor({ timeout: 15000 });
  check((await tabs(page))[0] === 'Scénarios', 'français gardé après rechargement (réglage du compte)');
  check((await api(page, 'GET', '/api/auth/status')).body.user.locale === 'fr', 'user.locale = fr');
  const notFound = await api(page, 'GET', '/api/campaigns/999999');
  check(notFound.body.detail === 'Campagne introuvable', `message du serveur dans la langue du compte : ${notFound.body.detail}`);

  await openMenu(page);
  await menu.getByRole('menuitemradio', { name: 'Automatique' }).click();
  await page.waitForFunction(() => document.querySelector('.module-tabs a span')?.textContent === 'Scenarios');
  check(await lang(page) === 'en', 'Automatique : de nouveau la langue du navigateur (anglais)');
  check((await api(page, 'GET', '/api/auth/status')).body.user.locale === '', 'user.locale vide (automatique)');

  // --- 3. Écran de connexion : sélecteur, choix gardé dans le navigateur ----------------------------------------
  await menu.getByRole('menuitem', { name: 'Sign out' }).click();
  await page.locator('#login-username').waitFor({ timeout: 15000 });
  const select = page.locator('[data-locale-select]');
  check(await select.getAttribute('aria-label') === 'Language', 'sélecteur « Language » sur l\'écran de connexion');
  await select.selectOption('fr');
  await page.getByRole('button', { name: 'Se connecter' }).waitFor({ timeout: 5000 });
  check(await lang(page) === 'fr', 'écran de connexion en français après le choix');
  await page.reload();
  await page.getByRole('button', { name: 'Se connecter' }).waitFor({ timeout: 15000 });
  check(await page.evaluate(() => localStorage.getItem('maze:locale')) === 'fr', 'choix gardé dans ce navigateur');
  await select.selectOption('');
  await page.getByRole('button', { name: 'Sign in' }).waitFor({ timeout: 5000 });
  check(await page.evaluate(() => localStorage.getItem('maze:locale')) === null, 'Automatique : choix retiré');

  // --- 4. Navigateur en français, compte en anglais ------------------------------------------------------------
  await login(page);
  await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  check((await api(page, 'PUT', '/api/auth/locale', { locale: 'en' })).status === 200, 'compte réglé sur English');
  const fr = await browser.newContext({ viewport: { width: 1280, height: 820 }, locale: 'fr-FR' });
  const other = await fr.newPage();
  other.on('pageerror', (e) => errors.push(`pageerror (fr): ${e.message}`));
  await other.goto(BASE + '/');
  await other.getByRole('button', { name: 'Se connecter' }).waitFor({ timeout: 15000 });
  await login(other, 'mauvais-mot-de-passe');
  await other.locator('.form-error[role=alert]').waitFor({ timeout: 15000 });
  check(await other.locator('.form-error[role=alert]').textContent() === 'Identifiant ou mot de passe incorrect',
    'navigateur en français : message du serveur en français');
  await login(other);
  await other.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  await other.goto(`${BASE}/#/c/${created.body.id}`);
  await other.locator('.module-tabs').waitFor({ timeout: 15000 });
  await other.waitForFunction(() => document.querySelector('.module-tabs a span')?.textContent === 'Scenarios');
  check(await lang(other) === 'en', 'le réglage du compte l\'emporte sur la langue du navigateur');
  await shot(other, 'langue-en-campagne');
  check((await api(other, 'PUT', '/api/auth/locale', { locale: '' })).status === 200, 'compte remis en automatique');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message.split('\n')[0]);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `langue-echec-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `LANGUE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'LANGUE ÉCHEC');
process.exit(ok && !errors.length ? 0 : 1);
