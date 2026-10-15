// Parcours navigateur de non-régression des fiches rédigées (onglet Scénarios, ScenariosView.jsx), écrit avant le
// découpage du composant (audit QUAL-10) : arbre des fiches (création depuis un modèle, sous-page, monter et
// descendre, déplacer vers, dupliquer, supprimer), titre et statut enregistrés, texte et sommaire, mention @ et son
// aperçu, commentaires (création, réponse, résolution), historique (version nommée, aperçu), mode lecture, menu de
// la fiche (modèle), fil d'Ariane, sous-pages, carte du scénario, fiche ouverte supprimée ailleurs.
//
// Base vide ou déjà initialisée. Usage : node scenarios.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

/** Attend qu'une condition sur l'API devienne vraie (enregistrements différés : titre, temps réel). */
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
  a.on('dialog', (d) => d.accept().catch(() => {}));
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

  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Scénarios ${TAG}` })).id;
  const vex = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Vex la Receleuse', summary: 'Elle sait tout.' });
  const docs = async () => (await api(a, 'GET', `/api/campaigns/${cid}/documents`)).documents;
  const byTitle = async (title) => (await docs()).find((d) => d.title === title);
  const tree = a.locator('aside.doc-tree');
  const treeItem = (title) => tree.locator('.doc-tree__item', { has: a.locator('.doc-tree__label', { hasText: new RegExp(`^${title.replace(/[()]/g, '\\$&')}$`) }) });
  const editor = a.locator('.doc .ProseMirror');
  const saved = () => a.locator('.save-state--saved').first().waitFor({ timeout: 15000 });
  const modal = (title) => a.locator('.modal', { has: a.locator('h2', { hasText: title }) });
  const treeAction = async (title, action) => {
    await treeItem(title).locator('.doc-tree__more').click();
    await a.getByRole('menuitem', { name: action }).click();
  };

  await a.goto(`${BASE}/#/c/${cid}/scenarios`);
  await a.reload();
  await a.locator('.doc__title', { hasText: `Scénarios ${TAG}` }).waitFor({ timeout: 15000 });
  check(await tree.locator('.doc-tree__root.is-active').isVisible(), 'fiche de campagne ouverte par défaut');

  // 1. Scénario depuis un modèle fourni, puis un second vierge.
  await tree.getByRole('button', { name: 'Nouveau scénario' }).click();
  const create = modal('Nouveau scénario');
  await create.locator('#new-doc-title').fill('Le bal masqué');
  const firstBuiltin = create.locator('.tpl-card').nth(1);
  const builtinName = await firstBuiltin.locator('.tpl-card__name').innerText();
  await firstBuiltin.click();
  await create.getByRole('button', { name: 'Créer' }).click();
  await treeItem('Le bal masqué').waitFor({ timeout: 10000 });
  await editor.waitFor({ timeout: 15000 });
  const bal = await byTitle('Le bal masqué');
  check(bal && bal.kind === 'scenario' && a.url().endsWith(`/scenarios/${bal.id}`), `scénario créé et ouvert (modèle « ${builtinName} »)`);
  check((await api(a, 'GET', `/api/documents/${bal.id}`)).content.length > 20, 'contenu du modèle repris');
  await tree.getByRole('button', { name: 'Nouveau scénario' }).click();
  await modal('Nouveau scénario').locator('#new-doc-title').fill('La filature');
  await modal('Nouveau scénario').getByRole('button', { name: 'Créer' }).click();
  await treeItem('La filature').waitFor({ timeout: 10000 });
  const filature = await until(() => byTitle('La filature'), 'second scénario');

  // 2. Titre et statut, enregistrés par l'API ; arbre à jour.
  await editor.waitFor({ timeout: 15000 });
  const title = a.locator('textarea.doc__title');
  await title.fill('La filature de Rathbone');
  await treeItem('La filature de Rathbone').waitFor({ timeout: 10000 });
  await until(async () => (await api(a, 'GET', `/api/documents/${filature.id}`)).title === 'La filature de Rathbone', 'titre enregistré');
  check(true, 'titre modifié : enregistré et repris dans l’arbre');
  await a.locator('.status-pick__opt.status--pret').click();
  await until(async () => (await api(a, 'GET', `/api/documents/${filature.id}`)).status === 'pret', 'statut enregistré');
  check(await treeItem('La filature de Rathbone').locator('.status-dot.status--pret').count() === 1, 'statut « Prêt » : enregistré et montré dans l’arbre');

  // 3. Texte, titre de section, sommaire, mention @.
  await editor.click();
  await a.keyboard.type('## Acte I');
  await a.keyboard.press('Enter');
  await a.keyboard.type('Rathbone quitte le port avec ');
  await a.keyboard.type('@Vex');
  await a.locator('.suggest [role=option]', { hasText: 'Vex la Receleuse' }).first().waitFor({ timeout: 5000 });
  await a.keyboard.press('Enter');
  await a.keyboard.type(' et une lettre scellée.');
  await editor.locator('h2', { hasText: 'Acte I' }).waitFor();
  await saved();
  await until(async () => {
    const d = await api(a, 'GET', `/api/documents/${filature.id}`);
    return d.content.includes(`data-mention="npc:${vex.id}"`) && d.content.includes('lettre scellée');
  }, 'texte et mention enregistrés');
  check(true, 'texte, titre de section et mention @ enregistrés');
  await a.locator('.doc-side__tabs [role=tab]', { hasText: 'Sommaire' }).click();
  await a.locator('.doc-toc a', { hasText: 'Acte I' }).waitFor({ timeout: 5000 });
  check(true, 'sommaire : titre de section listé');
  await editor.locator('.mention', { hasText: 'Vex la Receleuse' }).click();
  const card = a.locator('.mention-card');
  await card.waitFor({ timeout: 5000 });
  check((await card.innerText()).includes('Elle sait tout.'), 'aperçu de la mention : résumé de la fiche');
  await a.keyboard.press('Escape');
  await card.waitFor({ state: 'detached', timeout: 5000 });
  const mapLink = a.locator('.doc__bar a', { hasText: 'Carte du scénario' });
  check((await mapLink.getAttribute('href')) === `#/c/${cid}/carte?scenario=${filature.id}`, 'lien « Carte du scénario »');

  // 4. Commentaires : sélection, fil, réponse, résolution.
  await editor.locator('p', { hasText: 'lettre scellée' }).dblclick({ position: { x: 20, y: 8 } });
  await a.locator('.comment-fab').click();
  const comments = a.locator('.doc-side');
  await comments.locator('textarea[placeholder="Votre commentaire…"]').fill('Qui a scellé la lettre ?');
  await comments.getByRole('button', { name: 'Commenter' }).click();
  await comments.locator('.cthread', { hasText: 'Qui a scellé la lettre ?' }).waitFor({ timeout: 5000 });
  await until(async () => (await api(a, 'GET', `/api/documents/${filature.id}/comments`)).length === 1, 'fil enregistré');
  check(await a.locator('.doc-side__tabs .doc-side__badge').innerText() === '1', 'fil ouvert compté dans l’onglet');
  await editor.locator('[data-comment]').first().waitFor({ timeout: 5000 });
  // Fil actif (celui qu'on vient d'écrire) : la réponse se tape directement.
  await comments.locator('textarea[placeholder="Répondre…"]').fill('Le gouverneur.');
  await comments.getByRole('button', { name: 'Répondre' }).click();
  await comments.getByText('Le gouverneur.').waitFor({ timeout: 5000 });
  await comments.locator('button[title="Résoudre"]').first().click();
  await until(async () => (await api(a, 'GET', `/api/documents/${filature.id}/comments`))[0].resolved, 'fil résolu');
  await a.locator('.doc-side__tabs .doc-side__badge').waitFor({ state: 'detached', timeout: 5000 });
  check(true, 'commentaire, réponse et résolution');

  // 5. Historique : version nommée, aperçu, retour.
  await a.locator('.doc-side__tabs [role=tab]', { hasText: 'Historique' }).click();
  await a.getByRole('button', { name: 'Nommer l’état actuel' }).click();
  await a.locator('.doc-side input[aria-label="Nom de la version"]').fill('Avant la séance 1');
  await a.locator('.doc-side').getByRole('button', { name: 'Enregistrer' }).click();
  await a.locator('.history__open', { hasText: 'Avant la séance 1' }).waitFor({ timeout: 5000 });
  await a.locator('.history__open', { hasText: 'Avant la séance 1' }).click();
  await a.getByRole('button', { name: 'Revenir à la version actuelle' }).waitFor({ timeout: 5000 });
  check(await editor.isHidden(), 'aperçu d’une version : éditeur masqué');
  await a.getByRole('button', { name: 'Revenir à la version actuelle' }).click();
  await editor.waitFor({ state: 'visible', timeout: 5000 });
  check(true, 'historique : version nommée, aperçu puis retour');

  // 6. Mode lecture.
  await a.locator('.doc__bar button[aria-pressed]', { hasText: 'Lecture' }).click();
  check(await title.getAttribute('readonly') !== null && await editor.getAttribute('contenteditable') === 'false',
    'mode lecture : titre et texte non modifiables');
  await a.locator('.doc__bar button[aria-pressed="true"]', { hasText: 'Modifier' }).click();
  check(await editor.getAttribute('contenteditable') === 'true', 'mode édition rétabli');

  // 7. Menu de la fiche : enregistrer comme modèle, proposé ensuite à la création.
  await a.locator('.doc__more').click();
  await a.getByRole('menuitem', { name: 'Enregistrer comme modèle' }).click();
  await modal('Enregistrer comme modèle').locator('#tpl-name').fill(`Filature ${TAG}`);
  await modal('Enregistrer comme modèle').getByRole('button', { name: 'Enregistrer' }).click();
  await modal('Enregistrer comme modèle').waitFor({ state: 'detached', timeout: 5000 });
  check((await api(a, 'GET', '/api/templates')).templates.some((t) => t.name === `Filature ${TAG}`), 'modèle enregistré');

  // 8. Sous-page (bouton de la fiche), fil d'Ariane, cartes des sous-pages, modèle des MJ.
  await a.locator('.doc-children').getByRole('button', { name: 'Nouvelle sous-page' }).click();
  const sub = modal('Nouvelle sous-page');
  await sub.locator('.tpl-card', { hasText: `Filature ${TAG}` }).waitFor({ timeout: 5000 });
  await sub.locator('#new-doc-title').fill('Le quai');
  await sub.getByRole('button', { name: 'Créer' }).click();
  await treeItem('Le quai').waitFor({ timeout: 10000 });
  await editor.waitFor({ timeout: 15000 });
  const quai = await until(() => byTitle('Le quai'), 'sous-page créée');
  check(quai.kind === 'page' && quai.parent_id === filature.id, 'sous-page rangée sous le scénario');
  const crumbs = a.locator('.doc__crumbs');
  const crumbText = await until(async () => {
    const text = (await crumbs.innerText()).replace(/\s*\/\s*/g, ' / ').replace(/\s+/g, ' ');
    return text.includes('La filature de Rathbone / Le quai') && text;
  }, `fil d’Ariane : ${(await crumbs.innerText()).replace(/\s+/g, ' ')}`, 5000);
  check(crumbText.startsWith(`Scénarios ${TAG}`), `fil d’Ariane : ${crumbText}`);
  await crumbs.getByRole('button', { name: 'La filature de Rathbone' }).click();
  await a.locator('.doc-cards button', { hasText: 'Le quai' }).waitFor({ timeout: 10000 });
  check(true, 'retour par le fil d’Ariane : la sous-page est listée');

  // 9. Arbre : descendre, monter, déplacer vers, dupliquer, supprimer.
  const order = async () => (await docs()).filter((d) => d.kind === 'scenario').sort((x, y) => x.sort_order - y.sort_order).map((d) => d.title);
  check((await order()).join() === 'Le bal masqué,La filature de Rathbone', 'ordre initial des scénarios');
  await treeAction('Le bal masqué', 'Descendre');
  await until(async () => (await order()).join() === 'La filature de Rathbone,Le bal masqué', 'scénario descendu');
  await treeAction('Le bal masqué', 'Monter');
  await until(async () => (await order()).join() === 'Le bal masqué,La filature de Rathbone', 'scénario remonté');
  check(true, 'arbre : descendre puis monter');
  await treeAction('Le quai', 'Déplacer vers…');
  await modal('Déplacer « Le quai »').locator('.move-list button', { hasText: 'Le bal masqué' }).click();
  await until(async () => (await byTitle('Le quai')).parent_id === bal.id, 'page déplacée');
  check(true, 'arbre : « Déplacer vers… » un autre scénario');
  await treeAction('Le bal masqué', 'Dupliquer');
  await treeItem('Le bal masqué (copie)').waitFor({ timeout: 10000 });
  const copy = await until(() => byTitle('Le bal masqué (copie)'), 'copie');
  check((await docs()).some((d) => d.parent_id === copy.id && d.title === 'Le quai'), 'dupliquer : copie avec ses sous-pages, ouverte');
  await treeAction('Le bal masqué (copie)', 'Supprimer');
  await modal('Supprimer « Le bal masqué (copie) »').getByRole('button', { name: 'Supprimer' }).click();
  await treeItem('Le bal masqué (copie)').waitFor({ state: 'detached', timeout: 10000 });
  await until(async () => !(await byTitle('Le bal masqué (copie)')), 'copie supprimée');
  check(a.url().endsWith(`/scenarios/${bal.id}`) || a.url().endsWith('/scenarios'), 'fiche ouverte supprimée : retour à sa fiche parente');

  // 10. Fiche ouverte supprimée par un autre MJ.
  await treeItem('La filature de Rathbone').locator('.doc-tree__link').click();
  await editor.waitFor({ timeout: 15000 });
  await saved();
  await api(a, 'DELETE', `/api/documents/${filature.id}`);
  await a.waitForFunction(() => /\/scenarios$/.test(location.hash), null, { timeout: 15000 });
  await a.locator('.doc__title', { hasText: `Scénarios ${TAG}` }).waitFor({ timeout: 10000 });
  await a.reload();
  await treeItem('Le bal masqué').waitFor({ timeout: 15000 });
  check(await treeItem('La filature de Rathbone').count() === 0, 'fiche supprimée ailleurs : retour à la fiche de campagne');

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`SCÉNARIOS OK (compte ${USER}, mot de passe généré : ${PASSWORD})`);
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const page of browser.contexts().flatMap((c) => c.pages())) {
    await page.screenshot({ path: path.join(SHOTS, `scenarios-echec-${Date.now()}.png`) }).catch(() => {});
  }
  if (errors.length) console.error(errors.join('\n'));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
