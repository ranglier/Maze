// Parcours navigateur de la chronologie (module facultatif d'une campagne, migration 032) :
//   - désactivée par défaut : ni onglet, ni section sur les fiches ;
//   - activation par le menu de la campagne (« Activer la chronologie ») ; état vide, puis trois événements créés ;
//   - insertion au milieu (« + » entre deux événements), réordonnancement par « Déplacer vers… » et à la souris ;
//   - @mention d'un PNJ dans une description : section « Chronologie » de sa fiche, lien vers l'événement ;
//   - bascule Frise, ouverture d'une carte (panneau), flèches du clavier d'une carte à l'autre ;
//   - recherche Ctrl+K : résultat « Événements », ouverture de l'événement ;
//   - téléphone : accès par le menu de la campagne ; désactivation (confirmation) qui masque tout.
// Captures dans MAZE_E2E_SHOTS : liste, frise, frise sur téléphone.
//
// Base vide ou déjà initialisée. Usage : node chronologie.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

/** Avant une capture : notifications disparues, animations des panneaux terminées. */
async function settle(page) {
  await page.locator('.toast').first().waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
  await sleep(400);
}

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

  const name = `Chronique ${TAG}`;
  const campaign = await api(a, 'POST', '/api/campaigns', { name });
  const cid = campaign.id;
  check(campaign.timeline_enabled === false, 'chronologie désactivée à la création de la campagne');
  const ilse = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Ilse Varga', summary: 'Diplomate du Nord.' });
  const tabs = a.locator('nav.module-tabs');
  const timelineTab = tabs.getByRole('link', { name: 'Chronologie' });
  const titles = async () => (await api(a, 'GET', `/api/campaigns/${cid}/events`)).events.map((e) => e.title);
  const item = (title) => a.locator('.timeline-item', { has: a.locator('h3', { hasText: new RegExp(`^${title}$`) }) });
  const openMenu = async () => {
    await a.locator('.campaign-menu__trigger').click();
    await a.locator('.campaign-menu .popover').waitFor();
  };

  // --- 1. Désactivée par défaut : rien n'apparaît ---------------------------------------------------------------
  await a.goto(`${BASE}/#/c/${cid}/personnages/npc-${ilse.id}`);
  await a.reload();
  await a.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 15000 });
  check(await tabs.isVisible() && await timelineTab.count() === 0, 'onglet « Chronologie » absent par défaut');
  await a.goto(`${BASE}/#/c/${cid}/chronologie`);
  await a.waitForURL((u) => !u.hash.includes('chronologie'), { timeout: 5000 });
  check(true, 'adresse de la chronologie désactivée : retour à la fiche de campagne');

  // --- 2. Activation par le menu de la campagne ----------------------------------------------------------------
  await openMenu();
  await a.getByRole('menuitem', { name: 'Activer la chronologie' }).click();
  await a.waitForURL(/\/chronologie$/, { timeout: 10000 });
  await timelineTab.waitFor({ timeout: 5000 });
  check((await api(a, 'GET', '/api/campaigns')).find((c) => c.id === cid).timeline_enabled === true,
    'chronologie activée (campagne enregistrée)');
  await a.getByRole('heading', { name: 'Aucun événement' }).waitFor({ timeout: 10000 });
  check(true, 'état vide : « Aucun événement »');
  check(await a.getByRole('radio', { name: 'Frise' }).getAttribute('aria-checked') === 'true', 'grand écran : frise par défaut');
  await a.getByRole('radio', { name: 'Liste' }).click();

  // --- 3. Trois événements (liste) -------------------------------------------------------------------------------
  const fillForm = async (date, title) => {
    const form = a.locator('form.timeline-form');
    await form.waitFor({ timeout: 5000 });
    await form.getByLabel('Date').fill(date);
    await form.getByLabel('Titre').fill(title);
    await form.getByRole('button', { name: /^(Ajouter|Enregistrer)$/ }).click();
    await form.waitFor({ state: 'detached', timeout: 10000 });
    await item(title).waitFor({ timeout: 10000 });
  };
  await a.getByRole('button', { name: 'Ajouter un événement' }).click();
  await fillForm('Printemps 1203', 'La chute de Varn');
  await a.getByRole('button', { name: 'Ajouter un événement à la fin' }).click();
  await fillForm('Été 1203', 'Le traité des Brumes');
  await a.getByRole('button', { name: 'Ajouter un événement à la fin' }).click();
  await fillForm('Hiver 1204', 'Le couronnement');
  check((await titles()).join('|') === 'La chute de Varn|Le traité des Brumes|Le couronnement', 'trois événements créés, dans l’ordre');

  // --- 4. Insertion au milieu -----------------------------------------------------------------------------------
  // « + » juste avant « Le couronnement » (le 3e bouton d'insertion de la liste).
  await a.locator('.timeline-insert:not(.timeline-insert--end)').nth(2).locator('button').click();
  await fillForm('Automne 1203', 'Le siège de Morne');
  check((await titles()).join('|') === 'La chute de Varn|Le traité des Brumes|Le siège de Morne|Le couronnement',
    'événement inséré entre deux autres (before_id)');

  // --- 5. Réordonnancement : « Déplacer vers… », puis glisser-déposer ----------------------------------------------
  await item('Le couronnement').getByRole('button', { name: 'Déplacer vers…' }).click();
  const move = a.locator('.modal', { has: a.locator('h2', { hasText: 'Déplacer « Le couronnement »' }) });
  await move.getByRole('button', { name: 'Avant « Printemps 1203, La chute de Varn »' }).click();
  await until(async () => (await titles())[0] === 'Le couronnement', 'ordre enregistré');
  check((await titles()).join('|') === 'Le couronnement|La chute de Varn|Le traité des Brumes|Le siège de Morne',
    '« Déplacer vers… » : événement remonté en tête');
  await item('Le siège de Morne').locator('.timeline-grip').dragTo(item('La chute de Varn'), { targetPosition: { x: 60, y: 4 } });
  await until(async () => (await titles())[1] === 'Le siège de Morne', 'ordre après glisser-déposer');
  check((await titles()).join('|') === 'Le couronnement|Le siège de Morne|La chute de Varn|Le traité des Brumes',
    'glisser-déposer à la souris : ordre enregistré');
  const domOrder = await a.locator('.timeline-item h3').allInnerTexts();
  check(domOrder.join('|') === (await titles()).join('|'), 'liste affichée dans l’ordre enregistré');

  // --- 6. @mention d'un PNJ, étiquette ---------------------------------------------------------------------------
  await item('Le traité des Brumes').getByRole('button', { name: 'Modifier' }).click();
  const form = a.locator('form.timeline-form');
  await form.waitFor();
  await form.locator('.ProseMirror').click();
  await a.keyboard.type('Signé par ');
  await a.keyboard.type('@Ilse');
  await a.locator('.suggest [role=option]', { hasText: 'Ilse Varga' }).first().waitFor({ timeout: 5000 });
  await a.keyboard.press('Enter');
  await a.keyboard.type(' au nom du Nord.');
  await form.locator('.tag-input input').fill('Diplomatie');
  await form.locator('.tag-input input').press('Enter');
  await form.getByRole('button', { name: 'Enregistrer' }).click();
  await form.waitFor({ state: 'detached', timeout: 10000 });
  const traite = (await api(a, 'GET', `/api/campaigns/${cid}/events`)).events.find((e) => e.title === 'Le traité des Brumes');
  check(traite.mentions.some(([k, id]) => k === 'npc' && id === ilse.id) && traite.tags.includes('Diplomatie'),
    'description avec @mention et étiquette enregistrées');
  await item('Le traité des Brumes').locator('.timeline-mention', { hasText: 'Ilse Varga' }).waitFor({ timeout: 5000 });
  check(true, 'pastille de la fiche citée sur l’événement');
  await a.locator('.timeline-filter', { hasText: 'Diplomatie' }).click();
  check(await a.locator('.timeline-item h3').count() === 1, 'filtre par étiquette : un seul événement');
  await a.locator('.timeline-filter', { hasText: 'Tous' }).click();
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'chronologie-liste.png') });

  await a.goto(`${BASE}/#/c/${cid}/personnages/npc-${ilse.id}`);
  const section = a.locator('aside.panel [data-entry-section="timeline"]');
  await section.waitFor({ timeout: 10000 });
  check((await section.innerText()).includes('Été 1203') && (await section.innerText()).includes('Le traité des Brumes'),
    'fiche du PNJ : section « Chronologie » avec l’événement qui le cite');
  await section.getByRole('link', { name: /Le traité des Brumes/ }).click();
  await a.waitForURL(/\/chronologie$/, { timeout: 10000 });
  await item('Le traité des Brumes').waitFor({ timeout: 10000 });
  check(true, 'lien de la fiche : événement ouvert dans la chronologie');

  // --- 7. Frise ---------------------------------------------------------------------------------------------------
  await a.getByRole('radio', { name: 'Frise' }).click();
  const frieze = a.locator('.frieze');
  await frieze.waitFor({ timeout: 5000 });
  check(await a.locator('.frieze__slot.is-above').count() === 2 && await a.locator('.frieze__slot.is-below').count() === 2,
    'frise : cartes alternées au-dessus et en dessous de l’axe');
  const card = (title) => a.locator('.frieze__open', { hasText: title });
  await card('Le traité des Brumes').click();
  const panel = a.locator('aside.timeline-panel');
  await panel.locator('.timeline-detail__title', { hasText: 'Le traité des Brumes' }).waitFor({ timeout: 5000 });
  check((await panel.innerText()).includes('au nom du Nord'), 'panneau : description complète');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'chronologie-frise.png') });
  await card('Le couronnement').focus();
  await a.keyboard.press('ArrowRight');
  check(await a.evaluate(() => document.activeElement?.textContent?.includes('Le siège de Morne')),
    'flèche droite : carte suivante');
  await a.keyboard.press('Enter');
  await panel.locator('.timeline-detail__title', { hasText: 'Le siège de Morne' }).waitFor({ timeout: 5000 });
  check(true, 'Entrée : panneau de la carte choisie au clavier');
  await panel.getByRole('button', { name: 'Modifier' }).click();
  await panel.locator('form.timeline-form').waitFor();
  check(await panel.getByLabel('Titre').inputValue() === 'Le siège de Morne', 'panneau : formulaire de modification');
  await panel.getByRole('button', { name: 'Annuler' }).click();

  // --- 8. Recherche Ctrl+K -------------------------------------------------------------------------------------
  await a.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await a.keyboard.press('Control+k');
  const palette = a.locator('.palette');
  await palette.locator('input').fill('siège');
  const group = palette.locator('.palette__group', { has: a.locator('h3', { hasText: 'Événements' }) });
  await group.locator('.palette__item', { hasText: 'Le siège de Morne' }).waitFor({ timeout: 10000 });
  check((await group.innerText()).includes('Automne 1203'), 'recherche : groupe « Événements » avec la date');
  await a.keyboard.press('Enter');
  await palette.waitFor({ state: 'detached', timeout: 5000 });
  await a.waitForURL(/\/chronologie$/, { timeout: 10000 });
  await a.locator('.frieze__slot.is-selected', { hasText: 'Le siège de Morne' }).waitFor({ timeout: 5000 });
  check(true, 'résultat ouvert : événement sélectionné dans la frise');

  // --- 9. Téléphone : accès par le menu de la campagne, frise -------------------------------------------------
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'fr-FR', isMobile: true, hasTouch: true,
    storageState: await ctx.storageState() });
  const p = await phone.newPage();
  p.on('pageerror', (e) => errors.push(`pageerror (téléphone): ${e.message}`));
  await p.goto(`${BASE}/#/c/${cid}/personnages`);
  // Mode choisi sur grand écran (copié avec le stockage) oublié : défaut du téléphone en portrait, la liste.
  await p.evaluate(() => localStorage.removeItem('maze:timeline:mode'));
  await p.reload();
  await p.locator('.bottom-nav').waitFor({ timeout: 15000 });
  check(await p.locator('.bottom-nav a').count() === 4, 'téléphone : barre basse inchangée (sans chronologie)');
  await p.locator('.campaign-menu__trigger').click();
  await p.getByRole('menuitem', { name: 'Chronologie', exact: true }).click();
  await p.waitForURL(/\/chronologie$/, { timeout: 10000 });
  await p.locator('.timeline-item').first().waitFor({ timeout: 10000 });
  check(true, 'téléphone : chronologie ouverte depuis le menu de la campagne, en liste');
  await p.getByRole('radio', { name: 'Frise' }).click();
  await p.locator('.frieze__open', { hasText: 'Le traité des Brumes' }).click();
  await p.locator('aside.timeline-panel .timeline-detail__title').waitFor({ timeout: 5000 });
  await settle(p);
  await p.screenshot({ path: path.join(SHOTS, 'chronologie-frise-telephone.png') });
  await phone.close();

  // --- 10. Désactivation : tout est masqué, rien n'est supprimé -------------------------------------------------------
  await openMenu();
  await a.getByRole('menuitem', { name: 'Désactiver la chronologie' }).click();
  const dialog = a.locator('.modal', { has: a.locator('h2', { hasText: 'Désactiver la chronologie' }) });
  await dialog.waitFor({ timeout: 5000 });
  check((await dialog.innerText()).includes('4 événements sont conservés'), 'confirmation : événements conservés');
  await dialog.getByRole('button', { name: 'Désactiver' }).click();
  await a.waitForURL((u) => !u.hash.includes('chronologie'), { timeout: 10000 });
  await timelineTab.waitFor({ state: 'detached', timeout: 5000 });
  check(true, 'désactivée : onglet masqué, retour à la fiche de campagne');
  check((await titles()).length === 4, 'désactivée : événements toujours en base');
  await a.goto(`${BASE}/#/c/${cid}/personnages/npc-${ilse.id}`);
  await a.locator('aside.panel h2', { hasText: 'Ilse Varga' }).waitFor({ timeout: 10000 });
  await sleep(300);
  check(await a.locator('[data-entry-section="timeline"]').count() === 0, 'désactivée : section de la fiche masquée');
  await a.keyboard.press('Control+k');
  await a.locator('.palette input').fill('siège');
  await sleep(1200);
  check(await a.locator('.palette__group', { has: a.locator('h3', { hasText: 'Événements' }) }).count() === 0,
    'désactivée : aucun événement dans la recherche');
  await a.keyboard.press('Escape');
  await openMenu();
  check(await a.getByRole('menuitem', { name: 'Activer la chronologie' }).isVisible(), 'menu : « Activer la chronologie » proposé de nouveau');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `CHRONOLOGIE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'CHRONOLOGIE ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
