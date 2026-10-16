// Parcours navigateur des factions imbriquées (migration 031) et de la section « Membres » de la fiche faction :
//   - depuis le formulaire d'une faction (Membre de), la rendre membre d'une autre faction, avec un rôle ; les
//     factions qui formeraient un cycle ne sont pas proposées ;
//   - fiche de la sous-faction : section « Membre de » ; fiche de la faction parente : section « Membres » repliée
//     par défaut (compteur visible), dépliée à la souris puis repliée au clavier, PNJ et sous-faction distingués,
//     chefs en tête, deux colonnes ; de nouveau repliée sur une autre fiche ;
//   - carte des factions : arête de la sous-faction vers sa faction parente, masquée par le filtre des appartenances ;
//   - mêmes écrans en anglais (compte réglé sur English).
// Captures dans MAZE_E2E_SHOTS : fiche repliée et dépliée (français, anglais), carte avec l'imbrication.
//
// Base vide ou déjà initialisée. Usage : node factions-imbriquees.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

/** Arête de la carte (instance Cytoscape du conteneur) : null si absente. */
async function edgeInfo(page, id) {
  return page.evaluate((edgeId) => {
    const cy = document.querySelector('.graph__canvas')?._cyreg?.cy;
    const e = cy?.getElementById(edgeId);
    if (!e || e.empty()) return null;
    return { source: e.source().id(), target: e.target().id(), visible: e.visible(), sub: e.hasClass('faction-member'),
      label: e.data('label') };
  }, id);
}

/** Attend (5 s au plus) que l'arête soit visible ou masquée ; renvoie l'état obtenu. */
async function edgeVisible(page, id, expected) {
  await page.waitForFunction(([edgeId, want]) => {
    const e = document.querySelector('.graph__canvas')?._cyreg?.cy?.getElementById(edgeId);
    return e && !e.empty() && e.visible() === want;
  }, [id, expected], { timeout: 5000 }).catch(() => {});
  return (await edgeInfo(page, id))?.visible;
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

  const campaign = await api(a, 'POST', '/api/campaigns', { name: `Imbrications ${TAG}` });
  const cid = campaign.id;
  const faction = (name, color) => api(a, 'POST', `/api/campaigns/${cid}/factions`, { name, color });
  const council = await faction('Conseil du Port', '#7c8796');
  const watch = await faction('Garde du Port', '#4f7fb5');
  const house = await faction('Maison Valcourt', '#c9a45c');
  const npc = (name, title) => api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name, title });
  const hart = await npc('Capitaine Hart', 'Capitaine de la Garde');
  const tess = await npc('Tess Vif-Argent', 'Sergent');
  const mara = await npc('Mara Lenoir', 'Greffière du Conseil');
  const aurele = await npc('Aurèle de Valcourt', 'Chef de maison');
  await api(a, 'POST', '/api/memberships', { npc_id: hart.id, faction_id: watch.id, role: 'Capitaine', is_leader: true });
  await api(a, 'POST', '/api/memberships', { npc_id: tess.id, faction_id: watch.id, role: 'Sergent' });
  await api(a, 'POST', '/api/memberships', { npc_id: mara.id, faction_id: council.id, role: 'Greffière' });
  await api(a, 'POST', '/api/memberships', { npc_id: aurele.id, faction_id: council.id, role: 'Doyen', is_leader: true });
  await api(a, 'POST', '/api/memberships', { npc_id: aurele.id, faction_id: house.id, is_leader: true });
  // Maison Valcourt déjà membre de la Garde (pour vérifier qu'aucun cycle n'est proposé ensuite).
  await api(a, 'POST', '/api/memberships', { member_faction_id: house.id, faction_id: watch.id });

  const sheet = (f) => `${BASE}/#/c/${cid}/personnages/faction-${f.id}`;
  const panel = a.locator('aside.panel');
  const form = a.locator('form.sheet-form');

  // --- Formulaire de la Garde : membre du Conseil -------------------------------------------------------------
  await a.goto(sheet(watch));
  await a.reload();
  await panel.locator('h2', { hasText: 'Garde du Port' }).waitFor({ timeout: 15000 });
  await panel.locator('.panel__actions').getByRole('button', { name: 'Modifier' }).click();
  await form.waitFor({ timeout: 10000 });
  const parents = form.locator('fieldset.memberships--parents');
  check(await parents.locator('legend').innerText() === 'Membre de', 'formulaire : rubrique « Membre de »');
  check(await parents.getByText('Cette faction n’est membre d’aucune autre faction.').isVisible(), 'aucune faction parente au départ');
  const select = parents.locator('select[aria-label="Faction parente à ajouter"]');
  const offered = await select.locator('option').allInnerTexts();
  check(offered.includes('Conseil du Port') && !offered.includes('Garde du Port') && !offered.includes('Maison Valcourt'),
    `ni elle-même ni sa sous-faction proposées (${offered.join(', ')})`);
  await select.selectOption({ label: 'Conseil du Port' });
  await parents.getByRole('button', { name: 'Ajouter' }).click();
  const row = parents.locator('.memberships__row', { hasText: 'Conseil du Port' });
  await row.waitFor({ timeout: 10000 });
  await row.locator('input[aria-label="Rôle dans Conseil du Port"]').fill('Bras armé');
  await row.locator('input[aria-label="Rôle dans Conseil du Port"]').press('Tab');
  await a.waitForResponse((r) => r.url().includes('/api/memberships/') && r.request().method() === 'PATCH');
  let graph = await api(a, 'GET', `/api/campaigns/${cid}/graph`);
  const nested = graph.memberships.find((m) => m.member_faction_id === watch.id && m.faction_id === council.id);
  check(nested && nested.role === 'Bras armé' && nested.npc_id === null, 'appartenance créée depuis l’interface, avec son rôle');
  await form.getByRole('button', { name: 'Annuler' }).click();
  await form.waitFor({ state: 'detached', timeout: 10000 });

  // --- Fiche de la Garde : « Membre de » ----------------------------------------------------------------------
  const memberOf = panel.locator('[data-entry-section="member-of"]');
  await memberOf.waitFor({ timeout: 10000 });
  check(await memberOf.locator('.chip', { hasText: 'Conseil du Port' }).innerText() === 'Conseil du Port\nBras armé'
    || (await memberOf.locator('.chip').innerText()).replace(/\s+/g, ' ') === 'Conseil du Port Bras armé',
  'fiche de la sous-faction : « Membre de » Conseil du Port (Bras armé)');
  await panel.screenshot({ path: path.join(SHOTS, 'fiche-sous-faction-membre-de-fr.png') });

  // --- Fiche du Conseil : section « Membres » repliée par défaut ----------------------------------------------
  await memberOf.locator('.chip', { hasText: 'Conseil du Port' }).click();
  await panel.locator('h2', { hasText: 'Conseil du Port' }).waitFor({ timeout: 10000 });
  const members = panel.locator('[data-entry-section="members"]');
  const toggle = members.getByRole('button', { name: /Membres/ });
  const body = members.locator('.panel-section__body');
  check(await toggle.getAttribute('aria-expanded') === 'false', 'section Membres repliée par défaut (aria-expanded)');
  check(await body.isHidden(), 'liste des membres masquée');
  check(await toggle.getAttribute('aria-controls') === await body.getAttribute('id'), 'aria-controls désigne la liste');
  check(await members.locator('.count').innerText() === '3', 'compteur visible dans l’en-tête repliée (2 PNJ + 1 sous-faction)');
  check(await toggle.getAttribute('aria-label') === 'Membres : 2 PNJ et 1 sous-faction', 'détail du compteur (nom du bouton)');
  await members.screenshot({ path: path.join(SHOTS, 'fiche-faction-repliee-fr.png') });
  await panel.screenshot({ path: path.join(SHOTS, 'fiche-faction-panneau-repliee-fr.png') });

  await toggle.click();
  check(await toggle.getAttribute('aria-expanded') === 'true' && await body.isVisible(), 'dépliée au clic');
  const items = members.locator('.member-list > li');
  const names = await members.locator('.member-list__name').allInnerTexts();
  check(JSON.stringify(names) === JSON.stringify(['Aurèle de Valcourt', 'Mara Lenoir', 'Garde du Port']),
    `chef en tête, puis PNJ, puis sous-faction (${names.join(', ')})`);
  const sub = items.filter({ hasText: 'Garde du Port' });
  check(await sub.locator('.member-list__tag').innerText() === 'Sous-faction', 'sous-faction distinguée par un libellé');
  check(await sub.locator('.member-list__seal').count() === 1, 'sous-faction : sceau à sa couleur, pas un portrait');
  check(await items.first().locator('svg title').textContent() === 'Dirigeant', 'chef signalé');
  const [b1, b2] = await Promise.all([items.nth(0).boundingBox(), items.nth(1).boundingBox()]);
  check(Math.abs(b1.y - b2.y) < 2 && b2.x > b1.x + 100, 'deux colonnes dans le panneau');
  await members.screenshot({ path: path.join(SHOTS, 'fiche-faction-depliee-fr.png') });
  await panel.screenshot({ path: path.join(SHOTS, 'fiche-faction-panneau-depliee-fr.png') });

  // Clavier : Entrée replie, Espace déplie, Entrée replie.
  await toggle.focus();
  await a.keyboard.press('Enter');
  check(await toggle.getAttribute('aria-expanded') === 'false' && await body.isHidden(), 'repliée au clavier (Entrée)');
  await a.keyboard.press('Space');
  check(await toggle.getAttribute('aria-expanded') === 'true', 'dépliée au clavier (Espace)');
  await sub.getByRole('button').click();
  await panel.locator('h2', { hasText: 'Garde du Port' }).waitFor({ timeout: 10000 });
  step('Un clic sur la sous-faction ouvre sa fiche');
  const watchMembers = panel.locator('[data-entry-section="members"]');
  check(await watchMembers.getByRole('button', { name: /Membres/ }).getAttribute('aria-expanded') === 'false',
    'autre fiche : section de nouveau repliée');
  await a.goto(sheet(council));
  await panel.locator('h2', { hasText: 'Conseil du Port' }).waitFor({ timeout: 10000 });
  await toggle.click();
  await toggle.press('Enter');
  check(await body.isHidden(), 'refermée');

  // Panneau étroit (téléphone) : une seule colonne.
  await a.setViewportSize({ width: 390, height: 844 });
  await a.goto(sheet(council));
  await panel.locator('h2', { hasText: 'Conseil du Port' }).waitFor({ timeout: 10000 });
  await toggle.click();
  const [n1, n2] = await Promise.all([items.nth(0).boundingBox(), items.nth(1).boundingBox()]);
  check(n2.y > n1.y + 20 && Math.abs(n2.x - n1.x) < 2, 'une seule colonne sur téléphone');
  await members.screenshot({ path: path.join(SHOTS, 'fiche-faction-depliee-telephone-fr.png') });
  await a.setViewportSize({ width: 1400, height: 900 });

  // --- Carte des factions : arête de la sous-faction vers sa faction parente ----------------------------------
  const waitEdge = (id) => a.waitForFunction((edgeId) => !!document.querySelector('.graph__canvas')?._cyreg?.cy
    ?.getElementById(edgeId)?.length, id, { timeout: 15000 });
  await a.goto(`${BASE}/#/c/${cid}/carte?factions=1`);
  await waitEdge(`mem-${nested.id}`);
  let edge = await edgeInfo(a, `mem-${nested.id}`);
  check(edge.source === `faction-${watch.id}` && edge.target === `faction-${council.id}` && edge.sub && edge.visible,
    'carte des factions : arête Garde du Port > Conseil du Port, visible');
  check(edge.label === 'Bras armé', 'carte des factions : rôle en libellé');
  const other = graph.memberships.find((m) => m.member_faction_id === house.id);
  check((await edgeInfo(a, `mem-${other.id}`)).visible, 'carte des factions : seconde imbrication visible');

  // --- Carte d'un scénario : PNJ et factions, filtres, panneau latéral ----------------------------------------
  const mention = (kind, e) => `<a class="mention" data-mention="${kind}:${e.id}">${e.name}</a>`;
  const scenario = await api(a, 'POST', `/api/campaigns/${cid}/documents`, {
    title: 'La séance du Conseil',
    content: `<p>${[['faction', council], ['faction', watch], ['faction', house], ['npc', hart], ['npc', tess],
      ['npc', mara], ['npc', aurele]].map(([k, e]) => mention(k, e)).join(', ')}</p>` });
  await a.goto(`${BASE}/#/c/${cid}/carte?scenario=${scenario.id}`);
  await waitEdge(`mem-${nested.id}`);
  await a.locator('.stage__busy').waitFor({ state: 'detached', timeout: 20000 }).catch(() => {});
  await a.waitForTimeout(800);
  edge = await edgeInfo(a, `mem-${nested.id}`);
  check(edge.visible && edge.sub, 'carte du scénario : arête de la sous-faction visible');
  await a.locator('.graph').screenshot({ path: path.join(SHOTS, 'carte-scenario-imbrication.png') });
  await a.getByRole('button', { name: 'Options d’affichage' }).click();
  const popover = a.locator('.popover');
  await popover.getByLabel('Liens d’appartenance aux factions').uncheck();
  check(await edgeVisible(a, `mem-${nested.id}`, false) === false, 'filtre des appartenances : arête masquée');
  await popover.getByLabel('Liens d’appartenance aux factions').check();
  check(await edgeVisible(a, `mem-${nested.id}`, true) === true, 'filtre rétabli : arête visible');
  await popover.getByLabel('Conseil du Port').uncheck();
  check(await edgeVisible(a, `mem-${nested.id}`, false) === false, 'faction parente masquée : arête masquée');
  await popover.getByLabel('Conseil du Port').check();
  await popover.getByLabel('Garde du Port').uncheck();
  check(await edgeVisible(a, `mem-${nested.id}`, false) === false, 'sous-faction masquée : arête masquée');
  await popover.getByLabel('Garde du Port').check();
  await a.keyboard.press('Escape');
  // Panneau latéral de la carte : section repliée, puis deux colonnes une fois dépliée.
  await a.evaluate((id) => { document.querySelector('.graph__canvas')._cyreg.cy.getElementById(id).emit('tap'); },
    `faction-${council.id}`);
  const side = a.locator('aside.panel:not(.panel--page)');
  await side.locator('h2', { hasText: 'Conseil du Port' }).waitFor({ timeout: 10000 });
  const sideMembers = side.locator('[data-entry-section="members"]');
  const sideToggle = sideMembers.getByRole('button', { name: /Membres/ });
  check(await sideToggle.getAttribute('aria-expanded') === 'false', 'panneau de la carte : section repliée');
  await side.screenshot({ path: path.join(SHOTS, 'carte-panneau-replie-fr.png') });
  await sideToggle.click();
  const sideItems = sideMembers.locator('.member-list > li');
  const [s1, s2] = await Promise.all([sideItems.nth(0).boundingBox(), sideItems.nth(1).boundingBox()]);
  check(Math.abs(s1.y - s2.y) < 2 && s2.x > s1.x + 100, 'panneau de la carte (440 px) : deux colonnes');
  await side.screenshot({ path: path.join(SHOTS, 'carte-panneau-deplie-fr.png') });

  // --- Anglais ------------------------------------------------------------------------------------------------
  await api(a, 'PUT', '/api/auth/locale', { locale: 'en' });
  await a.goto(sheet(council));
  await a.reload();
  await panel.locator('h2', { hasText: 'Conseil du Port' }).waitFor({ timeout: 15000 });
  const toggleEn = members.getByRole('button', { name: /Members/ });
  check(await toggleEn.getAttribute('aria-expanded') === 'false', 'anglais : « Members » repliée');
  check(await toggleEn.getAttribute('aria-label') === 'Members: 2 NPCs and 1 sub-faction', 'anglais : détail du compteur');
  await members.screenshot({ path: path.join(SHOTS, 'fiche-faction-repliee-en.png') });
  await toggleEn.click();
  check(await members.locator('.member-list__tag').innerText() === 'Sub-faction', 'anglais : libellé « Sub-faction »');
  await members.screenshot({ path: path.join(SHOTS, 'fiche-faction-depliee-en.png') });
  await panel.screenshot({ path: path.join(SHOTS, 'fiche-faction-panneau-depliee-en.png') });
  await a.goto(sheet(watch));
  await panel.locator('h2', { hasText: 'Garde du Port' }).waitFor({ timeout: 10000 });
  check(await memberOf.locator('h3').innerText() === 'Member of', 'anglais : « Member of »');
  await panel.locator('.panel__actions').getByRole('button', { name: 'Edit' }).click();
  await form.waitFor({ timeout: 10000 });
  check(await parents.locator('legend').innerText() === 'Member of', 'anglais : rubrique du formulaire');
  await parents.screenshot({ path: path.join(SHOTS, 'formulaire-membre-de-en.png') });
  await api(a, 'PUT', '/api/auth/locale', { locale: '' });
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `FACTIONS IMBRIQUÉES OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'FACTIONS IMBRIQUÉES ÉCHEC');
process.exit(ok && !errors.length ? 0 : 1);
