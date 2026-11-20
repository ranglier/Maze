// Parcours navigateur de la corbeille d'une campagne (éléments supprimés gardés 30 jours) :
//   - corbeille vide, ouverte par le menu de la campagne ;
//   - PNJ supprimé depuis sa fiche : confirmation « placée dans la corbeille pendant 30 jours », notification avec
//     « Annuler », annulation : la fiche revient et se rouvre ;
//   - lieu avec un sous-lieu supprimé, puis restauré depuis la Corbeille (notification « Ouvrir ») ;
//   - fiche rédigée avec une sous-page supprimée, puis restaurée avec sa sous-page ;
//   - événement de la chronologie supprimé, « Annuler » au clavier ; supprimé de nouveau ;
//   - filtres par genre, suppression définitive (confirmation), corbeille vidée (confirmation) ;
//   - téléphone (390 px) : notification « Annuler », page de la corbeille sans défilement horizontal.
// Captures dans MAZE_E2E_SHOTS : notification « Annuler » et corbeille, sur ordinateur et sur téléphone.
//
// Base vide ou déjà initialisée. Usage : node corbeille.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}
const status = async (page, url) => (await page.request.fetch(BASE + url)).status();

async function until(fn, msg, timeout = 10000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fn();
    if (last) return last;
    await sleep(200);
  }
  throw new Error(`${msg} (délai dépassé)`);
}

/** Avant une capture : notifications disparues, animations terminées. */
async function settle(page) {
  await page.locator('.toast').first().waitFor({ state: 'detached', timeout: 15000 }).catch(() => {});
  await sleep(400);
}

/** Fenêtre de confirmation dont le titre contient `title` : texte vérifié, puis bouton `button`. */
async function confirmIn(page, title, button, expect) {
  const dialog = page.locator('.modal', { has: page.locator('h2', { hasText: title }) });
  await dialog.waitFor({ timeout: 5000 });
  const text = await dialog.innerText();
  for (const part of expect) check(text.includes(part), `confirmation « ${title} » : « ${part} »`);
  check(!/définitive/.test(text) || button === 'Supprimer définitivement' || button === 'Vider la corbeille',
    `confirmation « ${title} » : plus de « définitive » pour une mise à la corbeille`);
  await dialog.getByRole('button', { name: button, exact: true }).click();
  await dialog.waitFor({ state: 'detached', timeout: 5000 });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctx.newPage();
  a.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
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

  // Campagne de test : chronologie activée, un PNJ, un lieu et son sous-lieu, un scénario et sa sous-page, un événement.
  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Brumes ${TAG}` })).id;
  await api(a, 'PATCH', `/api/campaigns/${cid}`, { timeline_enabled: true });
  const ilse = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Ilse Varga', summary: 'Diplomate du Nord.' });
  const oriel = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Oriel Dast' });
  const guild = await api(a, 'POST', `/api/campaigns/${cid}/factions`, { name: 'Guilde du sel', color: '#8e5aa8' });
  const port = await api(a, 'POST', `/api/campaigns/${cid}/places`, { name: 'Port-Brume' });
  const quay = await api(a, 'POST', `/api/campaigns/${cid}/places`, { name: 'Quai aux sels', parent_id: port.id });
  const bal = await api(a, 'POST', `/api/campaigns/${cid}/documents`, { title: 'Le bal masqué', content: '<p>Les masques tombent.</p>' });
  const guests = await api(a, 'POST', `/api/campaigns/${cid}/documents`, { parent_id: bal.id, title: 'Les invités', content: '<p>Liste.</p>' });
  const treaty = await api(a, 'POST', `/api/campaigns/${cid}/events`, { title: 'Le traité des Brumes', date_label: 'An 1204' });
  const trash = async () => (await api(a, 'GET', `/api/campaigns/${cid}/trash`)).items;
  const openMenu = async (page = a) => {
    await page.locator('.campaign-menu__trigger').click();
    await page.locator('.campaign-menu .popover').waitFor();
  };
  const trashItem = (name, page = a) => page.locator('.trash-item', { has: page.locator('.trash-item__name', { hasText: new RegExp(`^${name}$`) }) });
  const toastWith = (text, page = a) => page.locator('.toast', { hasText: text });

  // --- 1. Corbeille vide, par le menu de la campagne -------------------------------------------------------------
  await a.goto(`${BASE}/#/c/${cid}`);
  await a.reload();
  await openMenu();
  await a.getByRole('menuitem', { name: 'Corbeille', exact: true }).click();
  await a.waitForURL(/\/corbeille$/, { timeout: 10000 });
  await a.getByRole('heading', { name: 'La corbeille est vide' }).waitFor({ timeout: 10000 });
  check(await a.getByRole('button', { name: 'Vider la corbeille' }).count() === 0, 'corbeille vide : état vide, pas de « Vider »');

  // --- 2. PNJ : confirmation, « Annuler » dans la notification, la fiche revient ---------------------------------
  await a.goto(`${BASE}/#/c/${cid}/personnages/npc-${ilse.id}`);
  await a.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 15000 });
  await a.getByRole('button', { name: 'Supprimer Ilse Varga' }).click();
  await confirmIn(a, 'Supprimer Ilse Varga', 'Supprimer', ['sera placée dans la corbeille pendant 30 jours']);
  const npcToast = toastWith('Ilse Varga supprimé');
  await npcToast.waitFor({ timeout: 10000 });
  await a.waitForURL((u) => u.hash === `#/c/${cid}/personnages`, { timeout: 10000 });
  check(await status(a, `/api/npcs/${ilse.id}`) === 404, 'PNJ supprimé (404)');
  const undo = npcToast.getByRole('button', { name: 'Annuler' });
  check(await undo.isVisible(), 'notification de suppression : bouton « Annuler »');
  await sleep(400);
  await a.screenshot({ path: path.join(SHOTS, 'corbeille-annuler-ordinateur.png') });
  await sleep(3500);
  check(await undo.isVisible(), 'notification avec action : encore affichée après 3,5 s');
  await undo.click();
  await toastWith('Suppression annulée').waitFor({ timeout: 10000 });
  await a.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 10000 });
  check(await status(a, `/api/npcs/${ilse.id}`) === 200, 'annulation : PNJ restauré, fiche rouverte');
  await until(async () => await a.locator('.places__body').getByText('Ilse Varga', { exact: true }).count() >= 2,
    'annulation : PNJ de retour dans l’index (et sa fiche)');
  check((await trash()).length === 0, 'annulation : corbeille de nouveau vide');

  // --- 3. Lieu avec un sous-lieu : supprimé, puis restauré depuis la Corbeille ---------------------------------------
  await a.goto(`${BASE}/#/c/${cid}/lieux/${port.id}`);
  await a.getByRole('button', { name: 'Supprimer Port-Brume' }).click();
  await confirmIn(a, 'Supprimer « Port-Brume »', 'Supprimer', ['placés dans la corbeille pendant 30 jours', 'Son sous-lieu remontera']);
  await toastWith('Lieu supprimé').waitFor({ timeout: 10000 });
  check(await status(a, `/api/places/${port.id}`) === 404, 'lieu supprimé (404)');
  await openMenu();
  await a.getByRole('menuitem', { name: 'Corbeille', exact: true }).click();
  await a.waitForURL(/\/corbeille$/, { timeout: 10000 });
  const portRow = trashItem('Port-Brume');
  await portRow.waitFor({ timeout: 10000 });
  const portText = await portRow.innerText();
  check(portText.includes('Lieu') && /Supprimé le .+ par /.test(portText) && /Effacé dans 30 jours|Effacé dans 29 jours/.test(portText),
    `corbeille : genre, date, auteur et délai (${portText.replace(/\s+/g, ' ')})`);
  await portRow.getByRole('button', { name: 'Restaurer « Port-Brume »' }).click();
  const restoredToast = toastWith('Élément restauré : « Port-Brume »');
  await restoredToast.waitFor({ timeout: 10000 });
  await portRow.waitFor({ state: 'detached', timeout: 10000 });
  check(await status(a, `/api/places/${port.id}`) === 200, 'lieu restauré depuis la corbeille');
  const quayNow = await api(a, 'GET', `/api/places/${quay.id}`);
  step(`sous-lieu après restauration : parent ${quayNow.parent_id ?? 'aucun'} (lieu restauré : ${port.id})`);
  await restoredToast.getByRole('button', { name: 'Ouvrir' }).click();
  await a.waitForURL((u) => u.hash === `#/c/${cid}/lieux/${port.id}`, { timeout: 10000 });
  check(true, 'notification « Ouvrir » : fiche du lieu restauré');

  // --- 4. Fiche rédigée avec une sous-page : supprimée puis restaurée avec sa sous-page ----------------------------
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${bal.id}`);
  const tree = a.locator('aside.doc-tree');
  const treeItem = (title) => tree.locator('.doc-tree__item', { has: a.locator('.doc-tree__label', { hasText: new RegExp(`^${title}$`) }) });
  await treeItem('Le bal masqué').waitFor({ timeout: 15000 });
  await treeItem('Le bal masqué').first().locator('.doc-tree__more').first().click();
  await a.getByRole('menuitem', { name: 'Supprimer' }).click();
  await confirmIn(a, 'Supprimer « Le bal masqué »', 'Supprimer', ['sa sous-page seront placées dans la corbeille pendant 30 jours']);
  await toastWith('Fiche supprimée').waitFor({ timeout: 10000 });
  await until(async () => await treeItem('Le bal masqué').count() === 0, 'fiche supprimée de l’arbre');
  check(await status(a, `/api/documents/${guests.id}`) === 404, 'sous-page supprimée avec sa fiche');
  await a.goto(`${BASE}/#/c/${cid}/corbeille`);
  const balRow = trashItem('Le bal masqué');
  await balRow.waitFor({ timeout: 10000 });
  check((await balRow.innerText()).includes('Fiche rédigée'), 'corbeille : genre « Fiche rédigée »');
  await balRow.getByRole('button', { name: 'Restaurer « Le bal masqué »' }).click();
  const balToast = toastWith('Élément restauré : « Le bal masqué »');
  await balToast.waitFor({ timeout: 10000 });
  check(await status(a, `/api/documents/${bal.id}`) === 200 && (await api(a, 'GET', `/api/documents/${guests.id}`)).parent_id === bal.id,
    'fiche rédigée restaurée avec sa sous-page');
  await balToast.getByRole('button', { name: 'Ouvrir' }).click();
  await a.waitForURL((u) => u.hash === `#/c/${cid}/scenarios/${bal.id}`, { timeout: 10000 });
  await treeItem('Les invités').waitFor({ timeout: 10000 });
  check(true, 'fiche restaurée ouverte, sous-page dans l’arbre');

  // --- 5. Événement : « Annuler » au clavier, puis suppression laissée dans la corbeille -------------------------
  await a.evaluate(() => localStorage.setItem('maze:timeline:mode', 'list'));
  await a.goto(`${BASE}/#/c/${cid}/chronologie`);
  const eventItem = a.locator('.timeline-item', { has: a.locator('h3', { hasText: 'Le traité des Brumes' }) });
  await eventItem.waitFor({ timeout: 15000 });
  const deleteEvent = async () => {
    await eventItem.hover();
    await eventItem.getByRole('button', { name: 'Supprimer', exact: true }).click();
    await confirmIn(a, 'Supprimer « Le traité des Brumes »', 'Supprimer', ['L’événement sera placé dans la corbeille pendant 30 jours.']);
    await toastWith('Événement supprimé').waitFor({ timeout: 10000 });
    await eventItem.waitFor({ state: 'detached', timeout: 10000 });
  };
  await deleteEvent();
  const eventUndo = toastWith('Événement supprimé').getByRole('button', { name: 'Annuler' });
  await eventUndo.focus();
  await a.keyboard.press('Enter');
  await eventItem.waitFor({ timeout: 10000 });
  check((await api(a, 'GET', `/api/campaigns/${cid}/events`)).events.some((e) => e.id === treaty.id),
    'événement : « Annuler » au clavier, de retour dans la chronologie');
  await settle(a);
  await deleteEvent();

  // --- 6. Plusieurs genres : filtres, capture ---------------------------------------------------------------------
  await api(a, 'DELETE', `/api/npcs/${oriel.id}`);
  await api(a, 'DELETE', `/api/factions/${guild.id}`);
  await a.goto(`${BASE}/#/c/${cid}/corbeille`);
  await trashItem('Oriel Dast').waitFor({ timeout: 10000 });
  check(await a.locator('.trash-item').count() === 3, 'corbeille : un PNJ, une faction, un événement');
  const filters = a.getByRole('radiogroup', { name: 'Filtrer la corbeille' });
  check(await filters.getByRole('radio').count() === 4, 'filtres : Tout, PNJ, Factions, Événements');
  await filters.getByRole('radio', { name: /^PNJ/ }).click();
  check(await a.locator('.trash-item').count() === 1 && await trashItem('Oriel Dast').isVisible(), 'filtre PNJ');
  await filters.getByRole('radio', { name: /^Tout/ }).click();
  check(await a.locator('.trash-item').count() === 3, 'filtre « Tout »');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'corbeille-ordinateur.png') });

  // --- 7. Suppression définitive -------------------------------------------------------------------------------------
  await trashItem('Oriel Dast').getByRole('button', { name: 'Supprimer définitivement « Oriel Dast »' }).click();
  await confirmIn(a, 'Supprimer définitivement « Oriel Dast »', 'Supprimer définitivement', ['sans retour possible']);
  await trashItem('Oriel Dast').waitFor({ state: 'detached', timeout: 10000 });
  check(!(await trash()).some((i) => i.kind === 'npc' && i.object_id === oriel.id) && await status(a, `/api/npcs/${oriel.id}`) === 404,
    'suppression définitive : hors de la corbeille, PNJ introuvable');

  // --- 8. Téléphone : notification « Annuler », page de la corbeille sans défilement horizontal -------------------
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'fr-FR', isMobile: true, hasTouch: true,
    storageState: await ctx.storageState() });
  const p = await phone.newPage();
  p.on('pageerror', (e) => errors.push(`pageerror (téléphone): ${e.message}`));
  await p.goto(`${BASE}/#/c/${cid}/personnages/npc-${ilse.id}`);
  await p.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 15000 });
  await p.getByRole('button', { name: 'Supprimer Ilse Varga' }).click();
  await confirmIn(p, 'Supprimer Ilse Varga', 'Supprimer', ['corbeille']);
  const phoneToast = toastWith('Ilse Varga supprimé', p);
  await phoneToast.waitFor({ timeout: 10000 });
  const box = await phoneToast.boundingBox();
  check(box && box.x >= 0 && box.x + box.width <= 390, 'téléphone : notification dans l’écran');
  await sleep(400);
  await p.screenshot({ path: path.join(SHOTS, 'corbeille-annuler-telephone.png') });
  await phoneToast.getByRole('button', { name: 'Annuler' }).tap();
  await p.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 10000 });
  check(await status(p, `/api/npcs/${ilse.id}`) === 200, 'téléphone : « Annuler » restaure le PNJ');
  await p.locator('.campaign-menu__trigger').click();
  await p.getByRole('menuitem', { name: 'Corbeille', exact: true }).click();
  await p.waitForURL(/\/corbeille$/, { timeout: 10000 });
  await trashItem('Le traité des Brumes', p).waitFor({ timeout: 10000 });
  await settle(p);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `téléphone : pas de défilement horizontal (${overflow} px)`);
  const restoreBox = await trashItem('Le traité des Brumes', p).getByRole('button', { name: 'Restaurer « Le traité des Brumes »' }).boundingBox();
  check(restoreBox && restoreBox.height >= 40 && restoreBox.x + restoreBox.width <= 390, 'téléphone : bouton « Restaurer » à portée du doigt');
  await p.screenshot({ path: path.join(SHOTS, 'corbeille-telephone.png'), fullPage: true });
  await phone.close();

  // --- 9. Vider la corbeille --------------------------------------------------------------------------------------
  await a.reload();
  await trashItem('Le traité des Brumes').waitFor({ timeout: 10000 });
  await a.getByRole('button', { name: 'Vider la corbeille' }).click();
  await confirmIn(a, 'Vider la corbeille', 'Vider la corbeille', ['Les 2 éléments de la corbeille seront effacés sans retour possible']);
  await a.getByRole('heading', { name: 'La corbeille est vide' }).waitFor({ timeout: 10000 });
  check((await trash()).length === 0 && await status(a, `/api/factions/${guild.id}`) === 404, 'corbeille vidée');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const pg of browser.contexts().flatMap((c) => c.pages())) {
    await pg.screenshot({ path: path.join(SHOTS, `echec-corbeille-${pg.viewportSize()?.width}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `CORBEILLE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'CORBEILLE ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
