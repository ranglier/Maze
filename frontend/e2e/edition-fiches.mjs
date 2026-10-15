// Parcours navigateur du lot 3 de l'audit, fiches PNJ, factions et lieux (formulaire d'édition) :
//   M2 (FRONT-3) : un champ que le MJ n'a pas touché, mais que Claude a réécrit pendant l'édition, ne doit pas être
//     remis à l'ancienne valeur à l'enregistrement (avant : le formulaire renvoyait tout champ différent de la fiche
//     relue, par exemple après l'ajout d'une faction).
//   M1 (DATA-3) : deux modifications du même champ ; le second enregistrement ne doit pas écraser le premier en
//     silence : le formulaire montre le conflit, garde la saisie, et le texte écrasé reste dans l'historique.
//   M3 (FRONT-4) : saisie en cours perdue sans confirmation (autre fiche de la liste, fond de la carte, fermeture de
//     l'onglet).
//
// Base vide ou déjà initialisée. Usage : node edition-fiches.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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
  // « Claude » : une autre session (le connecteur appelle les mêmes routes, sans révision de départ).
  const claudeCtx = await browser.newContext();
  const claude = await claudeCtx.newPage();
  await claude.request.post(BASE + '/api/auth/login', { data: { username: USER, password: PASSWORD } });

  const campaign = await api(a, 'POST', '/api/campaigns', { name: `Fiches ${TAG}` });
  const cid = campaign.id;
  await api(a, 'POST', `/api/campaigns/${cid}/factions`, { name: 'Guilde des ombres' });
  const hart = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, {
    name: 'Hart', notes: '<p>Notes de départ.</p>', secrets: '<p>Secret initial.</p>' });
  await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Ysolde' });
  const sheetUrl = `${BASE}/#/c/${cid}/personnages/npc-${hart.id}`;
  const form = a.locator('form.sheet-form');
  const openForm = async () => {
    await a.goto(sheetUrl);
    await a.locator('.panel__actions').getByRole('button', { name: 'Modifier' }).click();
    await form.waitFor({ timeout: 10000 });
  };
  const notesEditor = form.locator('.rich-field[aria-label="Notes"] .ProseMirror');
  const confirmDialog = a.locator('.modal', { hasText: 'Abandonner la saisie en cours' });

  const failures = [];
  const part = async (name, fn) => {
    try {
      await fn();
    } catch (e) {
      failures.push(`${name} : ${e.message.split('\n')[0]}`);
      console.error(`ÉCHEC ${name} :`, e.message.split('\n')[0]);
      await a.screenshot({ path: path.join(SHOTS, `fiches-echec-${Date.now()}.png`) }).catch(() => {});
      a.removeAllListeners('dialog');
      a.on('dialog', (d) => d.accept().catch(() => {}));
      await a.goto(`${BASE}/#/c/${cid}/personnages`);
      await a.reload();
      a.removeAllListeners('dialog');
    }
  };
  await a.goto(`${BASE}/#/c/${cid}/personnages`);
  await a.reload();

  // --- M2 : champ réécrit par Claude pendant l'édition ---------------------------------------------------------
  await part('M2', async () => {
    await openForm();
    await form.locator('#f-name').fill('Hart le Rouge');
    await api(claude, 'PATCH', `/api/npcs/${hart.id}`, { secrets: '<p>Secret écrit par Claude.</p>' });
    step('Claude réécrit le secret pendant l’édition');
    // Action enregistrée tout de suite (faction ajoutée) : le formulaire relit la fiche.
    await form.locator('select[aria-label="Faction à rejoindre"]').selectOption({ label: 'Guilde des ombres' });
    await form.locator('.memberships__add').getByRole('button', { name: 'Ajouter' }).click();
    await a.waitForResponse((r) => r.url().endsWith(`/api/npcs/${hart.id}`) && r.request().method() === 'GET');
    await sleep(300);
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    await form.waitFor({ state: 'detached', timeout: 10000 });
    const now = await api(a, 'GET', `/api/npcs/${hart.id}`);
    check(now.name === 'Hart le Rouge', 'nom changé par le MJ enregistré');
    check(now.secrets === '<p>Secret écrit par Claude.</p>', 'secret écrit par Claude pendant l’édition conservé');
  });

  // --- M1 : même champ modifié des deux côtés ------------------------------------------------------------------
  await part('M1', async () => {
    await openForm();
    await notesEditor.click();
    await a.keyboard.press('Control+End');
    await a.keyboard.type(' Ajout du MJ.', { delay: 10 });
    await api(claude, 'PATCH', `/api/npcs/${hart.id}`, { notes: '<p>Notes de départ.</p><p>Ajout de Claude.</p>' });
    step('Claude complète les notes pendant que le MJ les modifie');
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    const panel = a.locator('[data-entry-conflict]');
    await panel.waitFor({ timeout: 10000 });
    await a.screenshot({ path: path.join(SHOTS, 'm1-conflit.png') });
    check(await panel.locator('[data-conflict-field="notes"]').textContent().then((t) => t.includes('Ajout de Claude')),
      'conflit montré avec la version actuelle des notes');
    check((await notesEditor.textContent()).includes('Ajout du MJ'), 'saisie du MJ toujours dans le formulaire');
    let now = await api(a, 'GET', `/api/npcs/${hart.id}`);
    check(now.notes.includes('Ajout de Claude') && !now.notes.includes('Ajout du MJ'), 'rien d’écrasé tant que le MJ n’a pas choisi');
    await panel.getByRole('button', { name: 'Garder ma saisie' }).click();
    await form.getByRole('button', { name: 'Enregistrer' }).click();
    await form.waitFor({ state: 'detached', timeout: 10000 });
    now = await api(a, 'GET', `/api/npcs/${hart.id}`);
    check(now.notes.includes('Ajout du MJ'), 'saisie du MJ enregistrée après son choix');
    const { versions } = await api(a, 'GET', `/api/npcs/${hart.id}/versions`);
    const contents = [];
    for (const v of versions) contents.push((await api(a, 'GET', `/api/npcs/${hart.id}/versions/${v.id}`)).content.notes);
    check(contents.some((n) => n.includes('Ajout de Claude')), 'texte de Claude gardé dans l’historique de la fiche');
  });

  // --- M3 : saisie en cours quittée -----------------------------------------------------------------------------
  await part('M3 autre fiche', async () => {
    await a.goto(`${BASE}/#/c/${cid}/personnages`);   // entrée précédente de l'historique : la liste
    // Entrée sans rang dans history.state, comme celles créées avant la mise à jour dans un onglet ensuite rechargé
    // (Chromium les garde dans le même document) : « Retour » refusé ne doit pas reculer d'une entrée de plus.
    await sleep(300);
    await a.evaluate(() => history.replaceState(null, ''));
    await openForm();
    await form.locator('#f-name').fill('Hart, en cours de saisie');
    const depth = await a.evaluate(() => history.length);
    await a.locator('.doc-tree .doc-tree__link', { hasText: 'Ysolde' }).first().click();
    await confirmDialog.waitFor({ timeout: 5000 });
    step('Confirmation demandée avant d’ouvrir une autre fiche');
    await confirmDialog.getByRole('button', { name: 'Annuler' }).click();
    await sleep(300);
    check(a.url().endsWith(`/personnages/npc-${hart.id}`), 'adresse inchangée après « Annuler »');
    check(await form.locator('#f-name').inputValue() === 'Hart, en cours de saisie', 'saisie intacte après « Annuler »');
    // Aucune entrée en double dans l'historique : « Retour » mène à la liste (nouvelle confirmation), pas à la fiche.
    check(await a.evaluate(() => history.length) === depth + 1, 'au plus l’entrée « suivante » de la fiche refusée');
    await a.evaluate(() => history.back());
    await confirmDialog.waitFor({ timeout: 5000 });
    step('« Retour » du navigateur : confirmation demandée (l’entrée précédente est bien la liste)');
    await confirmDialog.getByRole('button', { name: 'Annuler' }).click();
    await sleep(300);
    check(a.url().endsWith(`/personnages/npc-${hart.id}`), 'adresse de la fiche remise après « Annuler » sur « Retour »');
    check(await form.locator('#f-name').inputValue() === 'Hart, en cours de saisie', 'saisie toujours intacte');
    await a.locator('.doc-tree .doc-tree__link', { hasText: 'Ysolde' }).first().click();
    await confirmDialog.getByRole('button', { name: 'Abandonner' }).click();
    await a.waitForURL(/\/personnages\/npc-\d+$/, { timeout: 5000 });
    check(!a.url().endsWith(`/npc-${hart.id}`), 'autre fiche ouverte après « Abandonner »');
  });

  await part('M3 fond de la carte', async () => {
    // Carte des factions : une seule faction, au centre ; panneau de sa fiche ouvert par un clic sur elle.
    await a.goto(`${BASE}/#/c/${cid}/carte?factions=1`);
    await a.locator('.graph canvas').first().waitFor({ timeout: 15000 });
    await sleep(1500);
    const box = await a.locator('.graph').boundingBox();
    await a.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await a.locator('.panel__actions').getByRole('button', { name: 'Modifier' }).click();
    await form.waitFor({ timeout: 10000 });
    await form.locator('#f-name').fill('Guilde des ombres, renommée');
    await a.mouse.click(box.x + 60, box.y + box.height - 60);
    await confirmDialog.waitFor({ timeout: 5000 });
    step('Confirmation demandée avant de fermer le panneau (clic sur le fond de la carte)');
    await confirmDialog.getByRole('button', { name: 'Annuler' }).click();
    check(await form.locator('#f-name').inputValue() === 'Guilde des ombres, renommée', 'saisie intacte dans le panneau');
  });

  await part('M3 fermeture de l’onglet', async () => {
    if (!(await form.count())) {
      await openForm();
      await form.locator('#f-name').fill('Hart, avant de fermer l’onglet');
    }
    let asked = null;
    a.on('dialog', (d) => { asked = d.type(); d.dismiss().catch(() => {}); });
    await a.close({ runBeforeUnload: true });
    await sleep(1000);
    check(asked === 'beforeunload', 'le navigateur prévient avant de fermer l’onglet');
  });
  if (failures.length) throw new Error(failures.join(' ; '));
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `FICHES OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'FICHES ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
