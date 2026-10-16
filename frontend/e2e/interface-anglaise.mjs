// Parcours navigateur de l'interface en anglais (src/i18n), navigateur en en-US sur une instance en français par
// défaut (MAZE_DEFAULT_LOCALE=fr) : la langue du navigateur suffit, et rien de français ne doit rester à l'écran.
//   1. Premier lancement ou connexion en anglais.
//   2. Campagne créée depuis l'interface : natures de lien et types de lieux créés en anglais (langue du créateur).
//   3. PNJ (fiche et formulaire), lieux (index, fiche, formulaire), carte des relations, scénario : textes anglais.
//   4. Éditeur : encadré Accroche inséré depuis la barre d'outils, étiquette « Read-aloud » (variable CSS).
//   5. Menu du compte : Français puis English, sans recharger la page ; l'étiquette de l'encadré suit.
// À chaque écran, un échantillon de textes français connus de l'interface fait échouer le parcours s'il apparaît
// (texte visible, title, aria-label, placeholder). Captures dans MAZE_E2E_SHOTS (anglais-*.png).
//
// Base vide ou déjà initialisée. Usage : node interface-anglaise.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `anglais-${name}.png`) });
const tabs = (page) => page.locator('.module-tabs a span').allTextContents();

// Échantillon de textes français de l'interface (libellés courants, onglets, champs, encadrés). Les données saisies
// par le parcours sont en anglais : toute occurrence vient d'un texte resté en dur ou d'une traduction manquante.
const FRENCH = /(?<![\p{L}])(Scénarios?|Personnages|Lieux|Enregistrer|Annuler|Modifier|Supprimer|Rechercher|Aucune?|Accroche|Fiches?|fiche|Nouveau|Nouvelle|Carte interactive|Index des lieux|Apparence|Brouillon|Sommaire|Historique|Commentaires|Ajouter|Fermer|Relations|Vivant|Résumé|Métier|Type de lieu|Rangé sous|Natures? de lien|chargement|Chargement)(?![\p{L}])/u;

async function frenchTexts(page) {
  const texts = await page.evaluate(() => {
    const out = [document.body.innerText, document.title];
    for (const el of document.querySelectorAll('[title], [aria-label], [placeholder]')) {
      for (const a of ['title', 'aria-label', 'placeholder']) if (el.getAttribute(a)) out.push(el.getAttribute(a));
    }
    return out;
  });
  return texts.flatMap((s) => s.split('\n')).filter((s) => FRENCH.test(s));
}

async function noFrench(page, where) {
  const found = await frenchTexts(page);
  check(found.length === 0, `${where} : aucun texte français${found.length ? ` (trouvé : ${found.slice(0, 5).join(' | ')})` : ''}`);
}

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data, headers: { 'Accept-Language': 'en-US' } });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

/** Étiquette dessinée par la feuille de style avant un encadré (content de ::before, guillemets retirés). */
const calloutLabel = (page, kind) => page.evaluate((k) => {
  const el = document.querySelector(`.doc .ProseMirror .callout[data-callout="${k}"]`);
  return el ? getComputedStyle(el, '::before').content.replace(/^"|"$/g, '') : null;
}, kind);

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'en-US' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('dialog', (d) => d.accept().catch(() => {}));

  // --- 1. Premier lancement ou connexion ---------------------------------------------------------------------
  await page.goto(BASE + '/');
  await page.locator('#setup-username, #login-username').first().waitFor({ timeout: 15000 });
  check(await page.evaluate(() => document.documentElement.lang) === 'en', '<html lang="en">');
  if (await page.locator('#setup-username').isVisible()) {
    await noFrench(page, 'premier lancement');
    step(`Création du premier administrateur « ${USER} »`);
    await page.fill('#setup-username', USER);
    await page.fill('#setup-name', 'Admin E2E');
    await page.fill('#setup-password', PASSWORD);
    await page.fill('#setup-confirm', PASSWORD);
    await page.getByRole('button', { name: 'Create the account and enter' }).click();
    await page.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
  } else {
    await noFrench(page, 'écran de connexion');
    await page.fill('#login-username', USER);
    await page.fill('#login-password', PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  }

  // --- 2. Campagne créée depuis l'interface ------------------------------------------------------------------
  const name = `Harbour ${TAG}`;
  const existing = (await api(page, 'GET', '/api/campaigns')).length;
  if (existing === 0) {
    await page.getByRole('button', { name: 'Create a campaign' }).click();
  } else {
    await page.locator('.module-tabs').waitFor({ timeout: 15000 });
    await page.locator('.campaign-menu__trigger').click();
    await page.getByRole('menuitem', { name: 'New campaign' }).click();
  }
  const dialog = page.locator('.modal', { has: page.locator('#campaign-name') });
  await dialog.locator('#campaign-name').fill(name);
  await noFrench(page, 'nouvelle campagne');
  await dialog.getByRole('button', { name: 'Create campaign' }).click();
  await page.locator('.module-tabs').waitFor({ timeout: 15000 });
  const cid = (await api(page, 'GET', '/api/campaigns')).find((c) => c.name === name).id;
  check(JSON.stringify(await tabs(page)) === JSON.stringify(['Scenarios', 'Graph', 'Characters', 'Places']),
    `onglets en anglais : ${(await tabs(page)).join(', ')}`);
  const types = (await api(page, 'GET', `/api/campaigns/${cid}/graph`)).relation_types.map((r) => r.label);
  check(types.includes('Ally') && types.includes('Enemy') && !types.includes('Allié'), `natures de lien en anglais : ${types.join(', ')}`);
  const kinds = (await api(page, 'GET', `/api/campaigns/${cid}/places`)).kinds.map((k) => k.label);
  check(kinds.includes('City') && kinds.includes('Building') && !kinds.includes('Ville'), `types de lieux en anglais : ${kinds.join(', ')}`);
  await noFrench(page, 'fiche de campagne');
  await shot(page, 'campagne');

  // Données de l'essai, en anglais (les données des MJ ne sont jamais traduites).
  const npc = await api(page, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Captain Hart', summary: 'An honest old soldier.' });
  const place = await api(page, 'POST', `/api/campaigns/${cid}/places`, { name: 'Watchtower', kind: 'batiment' });
  const doc = await api(page, 'POST', `/api/campaigns/${cid}/documents`, { title: 'The masked ball' });

  // --- 3. PNJ, lieux, carte des relations --------------------------------------------------------------------
  await page.goto(`${BASE}/#/c/${cid}/personnages/npc-${npc.id}`);
  await page.locator('h1, h2', { hasText: 'Captain Hart' }).first().waitFor({ timeout: 15000 });
  check(await page.getByRole('button', { name: 'Edit', exact: true }).first().isVisible(), 'fiche PNJ : bouton « Edit »');
  await noFrench(page, 'fiche PNJ');
  await page.goto(`${BASE}/#/c/${cid}/personnages/npc-${npc.id}?edit=1`);
  await page.locator('.sheet-form__title', { hasText: 'Edit sheet' }).waitFor({ timeout: 15000 });
  check(await page.getByText('Appearance', { exact: true }).first().isVisible(), 'formulaire PNJ : champ « Appearance »');
  await noFrench(page, 'formulaire PNJ');
  await shot(page, 'pnj');
  await page.getByRole('button', { name: 'Cancel' }).first().click();

  await page.goto(`${BASE}/#/c/${cid}/lieux`);
  await page.getByText('Campaign places').first().waitFor({ timeout: 15000 });
  await noFrench(page, 'index des lieux');
  await page.goto(`${BASE}/#/c/${cid}/lieux/${place.id}`);
  await page.locator('.place__kind', { hasText: 'Building' }).waitFor({ timeout: 15000 });
  await noFrench(page, 'fiche de lieu');
  await page.goto(`${BASE}/#/c/${cid}/lieux/${place.id}?edit=1`);
  await page.getByText('Read-aloud', { exact: true }).first().waitFor({ timeout: 15000 });
  check(await page.getByText('Filed under', { exact: true }).first().isVisible(), 'formulaire de lieu : champs en anglais');
  await noFrench(page, 'formulaire de lieu');
  await shot(page, 'lieu');
  await page.getByRole('button', { name: 'Cancel' }).first().click();

  await page.goto(`${BASE}/#/c/${cid}/cartes`);
  await page.getByRole('heading', { name: 'No maps yet' }).waitFor({ timeout: 15000 });
  await noFrench(page, 'cartes interactives');

  await page.goto(`${BASE}/#/c/${cid}/carte`);
  await page.locator('.module-tabs a.is-active, .module-tabs a[aria-current]', { hasText: 'Graph' }).first()
    .waitFor({ timeout: 15000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('.stage__loading'), null, { timeout: 15000 });
  await noFrench(page, 'carte des relations');

  // --- 4. Scénario : encadré Read-aloud -----------------------------------------------------------------------
  await page.goto(`${BASE}/#/c/${cid}/scenarios/${doc.id}`);
  const editor = page.locator('.doc .ProseMirror');
  await editor.waitFor({ timeout: 15000 });
  await editor.click();
  await page.keyboard.type('The music stops as the doors swing open.');
  await page.getByRole('button', { name: 'Read-aloud box: text to read to the players' }).click();
  await editor.locator('.callout[data-callout="read"]').waitFor({ timeout: 5000 });
  check(await calloutLabel(page, 'read') === 'Read-aloud', `étiquette de l'encadré : ${await calloutLabel(page, 'read')}`);
  await page.locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  await noFrench(page, 'éditeur de scénario');
  await shot(page, 'scenario');

  // --- 5. Langue du compte : Français puis English, sans rechargement ------------------------------------------
  const choose = async (label) => {
    await page.locator('.user-menu__trigger').click();
    const saved = page.waitForResponse((r) => r.url().endsWith('/api/auth/locale') && r.request().method() === 'PUT');
    await page.locator('.user-menu .popover').getByRole('menuitemradio', { name: label, exact: true }).click();
    check((await saved).status() === 200, `langue du compte : ${label}`);
    await page.keyboard.press('Escape');
  };
  await choose('Français');
  await page.waitForFunction(() => document.querySelector('.module-tabs a span')?.textContent === 'Scénarios');
  check(await calloutLabel(page, 'read') === 'Accroche', 'en français : étiquette « Accroche »');
  check((await frenchTexts(page)).length > 3, 'contrôle du détecteur : textes français repérés quand l\'interface est en français');
  await choose('English');
  await page.waitForFunction(() => document.querySelector('.module-tabs a span')?.textContent === 'Scenarios');
  check(await calloutLabel(page, 'read') === 'Read-aloud', 'de nouveau en anglais : étiquette « Read-aloud »');
  check((await api(page, 'GET', '/api/auth/status')).user.locale === 'en', 'user.locale = en');
  await noFrench(page, 'retour en anglais');
  await api(page, 'PUT', '/api/auth/locale', { locale: '' });
  check(true, 'compte remis en automatique');

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`INTERFACE ANGLAISE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`);
} catch (e) {
  console.error('ÉCHEC :', e.message.split('\n')[0]);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `anglais-echec-${Date.now()}.png`) }).catch(() => {});
  }
  if (errors.length) console.error(errors.join('\n'));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
