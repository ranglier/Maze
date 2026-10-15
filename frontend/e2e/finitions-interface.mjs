// Parcours navigateur du lot 6 de l'audit (finitions), partie interface :
//   SEC-3   modèles de fiches visibles par tous les MJ de l'instance (champ shared de l'API) : liste des modèles,
//           « Enregistrer comme modèle » ;
//   DATA-10 profondeur refusée (400) dans l'arbre des scénarios (création, « Déplacer vers… », glisser-déposer) et
//           dans celui des lieux (création, « Rangé dans », « Déplacer vers… », glisser-déposer) : message du
//           serveur, rien de déplacé, arbre inchangé ;
//   PERF-9  connexion temps réel refusée (fermeture 4429) : raison affichée dans l'indicateur de la fiche, nouvel
//           essai automatique qui aboutit quand une place se libère ;
//   PERF-15 Administration > Sauvegardes : images d'avant une restauration interrompue (replaced_media), alerte plus
//           forte si media/ manque ou est vide ; images d'une restauration interrompue gardées (interrupted_restores) ;
//   FRONT-5 ancien champ en Markdown piégé (classes de l'interface, dialog, lien //hôte) : rien ne recouvre l'écran ;
//   FRONT-7 liaison lente simulée (page.routeWebSocket, chaque trame du serveur retardée) : la fiche dont le journal
//           arrive en plus de 10 s s'ouvre, sans nouvel essai ni nouvel envoi du journal ;
//   contre-revue : lien ouvert juste après la connexion, pendant l'arrivée de la liste des campagnes : adresse gardée
//           (plus remplacée par celle de la campagne).
//
// Base vide ou déjà initialisée. Usage : node finitions-interface.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire),
//             MAZE_E2E_DATA_DIR (dossier de données de l'instance, posé par run-all.sh : vrai dossier
//             .media-remplace-… ; sans lui, la réponse de /api/admin/backups/usage est complétée dans le navigateur).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const candidates = [...(process.env.NODE_PATH || '').split(':').filter(Boolean),
  path.join(process.env.HOME || '/', '.npm-global/lib/node_modules'), '/opt/npm-tools/node_modules'];
let chromium;
for (const dir of candidates) {
  try { ({ chromium } = require(path.join(dir, 'playwright'))); break; } catch { /* suivant */ }
}
if (!chromium) throw new Error('Module playwright introuvable (NODE_PATH ?)');

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SCHEMA = JSON.parse(readFileSync(path.join(FRONTEND, 'src/editor/schema-version.json'), 'utf8')).version;
const BASE = (process.argv[2] || process.env.MAZE_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const USER = process.env.MAZE_E2E_USER || 'admin';
const PASSWORD = process.env.MAZE_E2E_PASSWORD || `e2e-${randomBytes(9).toString('base64url')}`;
const SHOTS = process.env.MAZE_E2E_SHOTS || path.join(tmpdir(), 'maze-e2e-shots');
const DATA = process.env.MAZE_E2E_DATA_DIR || '';
const TAG = randomBytes(3).toString('hex');
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const sleep = (ms) => new Promise((r) => { setTimeout(r, ms); });
const DOC_DEEP = 'Trop de niveaux de sous-pages (32 au plus)';
const PLACE_DEEP = 'Trop de niveaux de sous-lieux (32 au plus)';
const BUSY = 'Trop de fiches ouvertes en temps réel par ce compte';

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

/** Attend qu'une condition devienne vraie ; renvoie sa dernière valeur (fausse si le délai est dépassé). */
async function until(fn, timeout = 10000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fn().catch(() => null);
    if (last) return last;
    await sleep(150);
  }
  return last;
}

async function login(page) {
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('dialog', (d) => d.accept().catch(() => {}));
  await page.goto(BASE + '/');
  await page.locator('#setup-username, #login-username').first().waitFor({ timeout: 15000 });
  if (await page.locator('#setup-username').isVisible()) {
    step(`Création du premier administrateur « ${USER} »`);
    await page.fill('#setup-username', USER);
    await page.fill('#setup-name', 'Admin E2E');
    await page.fill('#setup-password', PASSWORD);
    await page.fill('#setup-confirm', PASSWORD);
    await page.getByRole('button', { name: 'Créer le compte et entrer' }).click();
    await page.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
  } else {
    await page.fill('#login-username', USER);
    await page.fill('#login-password', PASSWORD);
    await page.locator('form button[type=submit]').click();
    await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  }
}

/** Nouveau toast contenant `text` (attendu), marqué comme vu pour ne pas être compté par l'attente suivante. */
async function toastOf(page, text) {
  return until(() => page.evaluate((t) => {
    const el = [...document.querySelectorAll('.toast:not([data-e2e-seen])')].find((x) => x.textContent.includes(t));
    if (!el) return null;
    el.setAttribute('data-e2e-seen', '');
    return el.textContent;
  }, text), 8000);
}

/** Menu ⋯ d'une ligne de l'arbre, ligne d'abord ramenée au milieu de l'arbre (le menu s'ouvre sous elle). */
async function treeMenu(page, name, item) {
  const more = page.getByRole('button', { name: `Actions pour ${name}` });
  await more.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await more.click();
  await page.getByRole('menuitem', { name: item }).click();
}

/** Profondeur affichée d'une ligne de l'arbre (variable CSS --depth). */
const depthOf = (row) => row.evaluate((el) => Number(el.style.getPropertyValue('--depth')));

/** Glisser une ligne d'arbre au milieu d'une autre (dépôt « dedans »). */
async function dragInto(page, source, target) {
  await source.scrollIntoViewIfNeeded();
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  await source.dragTo(target, { targetPosition: { x: Math.min(60, box.width / 2), y: box.height / 2 } });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
let page;
try {
  // Grande hauteur : arbres de 32 niveaux affichés en entier, menus ⋯ des dernières lignes à l'écran.
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1700 }, locale: 'fr-FR' });
  page = await ctx.newPage();
  await login(page);
  const a = page;
  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Finitions ${TAG}` })).id;
  const { root_id: rootId } = await api(a, 'GET', `/api/campaigns/${cid}/documents`);
  const doc = async (title, parent = rootId) => (await api(a, 'POST', `/api/campaigns/${cid}/documents`,
    { parent_id: parent, title })).id;
  const parentOfDoc = async (id) => (await api(a, 'GET', `/api/campaigns/${cid}/documents`)).documents
    .find((d) => d.id === id).parent_id;
  step(`Campagne ${cid}`);
  await a.reload();   // liste des campagnes de l'interface relue

  // --- 1. SEC-3 : modèles visibles par tous les MJ de l'instance ------------------------------------------------
  const tpl = await api(a, 'POST', '/api/templates', { name: `Modèle partagé ${TAG}`, description: 'Pour la recette',
    content: '<h2>Lieu</h2><p>Ambiance.</p>' });
  check(tpl.shared === true, 'API : modèle créé avec shared: true');
  const fiche = await doc('Fiche modèle');
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${fiche}`);
  await a.locator('.doc .ProseMirror').waitFor({ timeout: 15000 });
  await a.getByRole('button', { name: 'Nouveau scénario' }).first().click();
  await a.locator('.tpl-picker').waitFor();
  await a.locator('.tpl-card', { hasText: `Modèle partagé ${TAG}` }).waitFor({ timeout: 8000 });
  const sharedHint = await a.locator('.tpl-picker__shared').innerText();
  check(sharedHint.includes('Visibles par tous les MJ de l’instance'), `liste des modèles : « ${sharedHint} »`);
  await a.locator('.modal').getByRole('button', { name: 'Annuler' }).click();
  await a.locator('.ProseMirror').click();
  await a.keyboard.type('Contenu du futur modèle.');
  await until(async () => (await a.locator('.save-state').innerText()) === 'Enregistré', 8000);
  await a.locator('.doc__more').click();
  await a.getByRole('menuitem', { name: 'Enregistrer comme modèle' }).click();
  const dialogText = await a.locator('.modal').innerText();
  check(dialogText.includes('visible par tous les MJ de l’instance'), '« Enregistrer comme modèle » : visible par tous les MJ de l’instance');
  await a.fill('#tpl-name', `Depuis la fiche ${TAG}`);
  await a.locator('.modal').getByRole('button', { name: 'Enregistrer' }).click();
  const saved = await until(async () => (await a.locator('.toast').allInnerTexts()).find((t) => t.startsWith('Modèle')), 8000);
  check(saved?.includes('visible par tous les MJ de l’instance'), `modèle enregistré : « ${saved} »`);

  // --- 2. DATA-10 : arbre des scénarios ------------------------------------------------------------------------
  // Chaîne de 32 niveaux (le scénario compte pour 1), et « Branche » (scénario avec une sous-page) à déplacer.
  const chain = [await doc('N1')];
  for (let i = 2; i <= 32; i += 1) chain.push(await doc(`N${i}`, chain[chain.length - 1]));
  const branche = await doc('Branche');
  await doc('Feuille de branche', branche);
  const count = async () => (await api(a, 'GET', `/api/campaigns/${cid}/documents`)).documents.length;
  const before = await count();
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${chain[30]}`);
  await a.locator('.doc .ProseMirror').waitFor({ timeout: 15000 });
  const docRow = (title) => a.locator('.doc-tree__item', { has: a.locator('.doc-tree__label', { hasText: new RegExp(`^${title}$`) }) });
  check(await depthOf(docRow('N32')) === 31 && await depthOf(docRow('Branche')) === 0, 'arbre des scénarios : 32 niveaux affichés');

  // Création sous le 32e niveau : refus, fenêtre gardée, rien de créé.
  await treeMenu(a, 'N32', 'Ajouter une sous-page');
  await a.fill('#new-doc-title', 'Trop profonde');
  await a.locator('.modal').getByRole('button', { name: 'Créer' }).click();
  check(await toastOf(a, DOC_DEEP), `sous-page du 32e niveau : « ${DOC_DEEP} »`);
  check(await a.locator('#new-doc-title').isVisible() && await a.locator('.modal').getByRole('button', { name: 'Créer' }).isEnabled(),
    'création refusée : fenêtre gardée, bouton Créer de nouveau actif');
  await a.locator('.modal').getByRole('button', { name: 'Annuler' }).click();
  check(await count() === before && await docRow('Trop profonde').count() === 0, 'création refusée : rien de créé, arbre inchangé');

  // « Déplacer vers… » : Branche (2 niveaux) sous N31 (elle irait au 33e) : refus, rien de déplacé.
  await treeMenu(a, 'Branche', 'Déplacer vers…');
  await a.locator('.move-list button', { hasText: /^N31$/ }).click();
  check(await toastOf(a, DOC_DEEP), `« Déplacer vers… » N31 : « ${DOC_DEEP} »`);
  await sleep(500);
  check(await parentOfDoc(branche) === rootId && await depthOf(docRow('Branche')) === 0
    && await depthOf(docRow('Feuille de branche')) === 1, '« Déplacer vers… » refusé : Branche reste un scénario');

  // Glisser-déposer Branche dans N31 : même refus.
  await dragInto(a, docRow('Branche'), docRow('N31'));
  check(await toastOf(a, DOC_DEEP), `glisser-déposer dans N31 : « ${DOC_DEEP} »`);
  await sleep(500);
  check(await parentOfDoc(branche) === rootId && await depthOf(docRow('Branche')) === 0
    && await a.locator('.doc-tree__item.is-drop-inside, .doc-tree__item.is-dragging').count() === 0,
  'glisser-déposer refusé : Branche à sa place, aucune ligne restée en cours de dépôt');
  // Un déplacement admis juste après : l'arbre suit.
  await dragInto(a, docRow('Branche'), docRow('N2'));
  await until(async () => await parentOfDoc(branche) === chain[1], 8000);
  check(await until(async () => await depthOf(docRow('Branche')) === 2, 8000), 'déplacement admis ensuite (sous N2) : arbre à jour');
  await a.screenshot({ path: path.join(SHOTS, 'finitions-scenarios.png') });

  // --- 3. DATA-10 : arbre des lieux ---------------------------------------------------------------------------
  const place = async (name, parent = null) => (await api(a, 'POST', `/api/campaigns/${cid}/places`,
    { name, kind: 'lieu', ...(parent ? { parent_id: parent } : {}) })).id;
  const parentOfPlace = async (id) => (await api(a, 'GET', `/api/places/${id}`)).parent_id ?? null;
  // Lieu de premier niveau P0 et 32 niveaux de sous-lieux (P1 à P32) : le plus profond admis.
  const pchain = [await place('P0')];
  for (let i = 1; i <= 32; i += 1) pchain.push(await place(`P${i}`, pchain[pchain.length - 1]));
  const pbranche = await place('Branche lieu');
  await place('Cave de la branche', pbranche);
  const placesCount = async () => (await api(a, 'GET', `/api/campaigns/${cid}/places`)).places.length;
  const pbefore = await placesCount();
  await a.goto(`${BASE}/#/c/${cid}/lieux/${pchain[32]}`);
  const placeRow = (name) => a.locator('.doc-tree__item', { has: a.locator('.doc-tree__label', { hasText: new RegExp(`^${name}$`) }) });
  await placeRow('P32').waitFor({ timeout: 15000 });

  // Création d'un sous-lieu de P32 (bouton Lieu : lieu affiché comme parent) : refus, fenêtre gardée.
  await a.locator('.topbar .toolbar').getByRole('button', { name: 'Lieu', exact: true }).click();
  await a.fill('#np-name', 'Trop profond');
  check(await a.locator('#np-parent').inputValue() === String(pchain[32]), 'nouveau lieu rangé dans P32');
  await a.locator('.modal').getByRole('button', { name: 'Créer' }).click();
  check(await toastOf(a, PLACE_DEEP), `sous-lieu de P32 : « ${PLACE_DEEP} »`);
  check(await a.locator('#np-name').isVisible(), 'création refusée : fenêtre gardée');
  await a.locator('.modal').getByRole('button', { name: 'Annuler' }).click();
  check(await placesCount() === pbefore, 'création refusée : rien de créé');

  // « Déplacer vers… » : Branche lieu (2 niveaux) dans P31 : refus.
  await treeMenu(a, 'Branche lieu', 'Déplacer vers…');
  await a.locator('.move-list button', { hasText: /^P31$/ }).click();
  check(await toastOf(a, PLACE_DEEP), `« Déplacer vers… » P31 : « ${PLACE_DEEP} »`);
  await sleep(500);
  check(await parentOfPlace(pbranche) === null && await depthOf(placeRow('Branche lieu')) === 0,
    '« Déplacer vers… » refusé : Branche lieu reste au premier niveau');

  // Glisser-déposer dans P31 : même refus, et le chemin du lieu n'est pas rouvert au rechargement suivant.
  await dragInto(a, placeRow('Branche lieu'), placeRow('P31'));
  check(await toastOf(a, PLACE_DEEP), `glisser-déposer dans P31 : « ${PLACE_DEEP} »`);
  await sleep(500);
  check(await parentOfPlace(pbranche) === null && await depthOf(placeRow('Branche lieu')) === 0
    && await placeRow('Cave de la branche').count() === 0, 'glisser-déposer refusé : Branche lieu à sa place, toujours replié');

  // « Rangé dans » (formulaire du lieu) : P31 refusé, sélection revenue au lieu actuel, reste de la saisie gardé.
  await a.goto(`${BASE}/#/c/${cid}/lieux/${pbranche}?edit=1`);
  await a.locator('#pl-parent').waitFor({ timeout: 15000 });
  await a.fill('#pl-summary', 'Résumé gardé après le refus');
  await a.locator('#pl-parent').selectOption(String(pchain[31]));
  await a.locator('.place-form').getByRole('button', { name: 'Enregistrer' }).click();
  check(await toastOf(a, PLACE_DEEP), `« Rangé dans » P31 : « ${PLACE_DEEP} »`);
  check(await a.locator('#pl-parent').inputValue() === '' && await a.locator('#pl-summary').inputValue() === 'Résumé gardé après le refus',
    '« Rangé dans » refusé : sélection revenue au lieu actuel, résumé gardé');
  check(await parentOfPlace(pbranche) === null, '« Rangé dans » refusé : rien d’enregistré');
  await a.locator('.place-form').getByRole('button', { name: 'Enregistrer' }).click();
  check(await until(async () => (await api(a, 'GET', `/api/places/${pbranche}`)).summary === 'Résumé gardé après le refus', 8000),
    'nouvel essai : résumé enregistré, lieu toujours au premier niveau');
  await a.screenshot({ path: path.join(SHOTS, 'finitions-lieux.png') });

  // --- 4. FRONT-5 : ancien champ en Markdown piégé -------------------------------------------------------------
  const npc = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Vex' });
  await api(a, 'PATCH', `/api/npcs/${npc.id}`, { notes: 'Notes de **Vex**, alliée de [[Vex]].\n\n'
    + '<div class="lightbox modal-backdrop">Session expirée : <a href="//evil.example/login">reconnectez-vous</a></div>\n\n'
    + '<dialog open class="palette-backdrop">faux dialogue</dialog>'
    + '<section data-entry-section="faux" data-entry-label="Sommaire piégé"><div data-entry-field="secrets">x</div></section>' });
  await a.goto(`${BASE}/#/c/${cid}/personnages/npc-${npc.id}`);
  await a.locator('.md', { hasText: 'Session expirée' }).waitFor({ timeout: 15000 });
  const md = a.locator('.md', { hasText: 'Session expirée' });
  check(await md.locator('strong', { hasText: 'Vex' }).count() === 1 && await md.locator('a.wikilink').count() === 1,
    'Markdown légitime affiché (gras, [[Nom]])');
  const cover = await a.evaluate(() => {
    const el = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
    return Boolean(el?.closest('.md')) && getComputedStyle(el).position === 'fixed';
  });
  check(await md.locator('.lightbox, .modal-backdrop, dialog, section, [data-entry-section]').count() === 0 && !cover,
    'bloc piégé : ni classes de l’interface, ni dialog, ni faux sommaire ; rien ne recouvre l’écran');
  check(await md.locator('a', { hasText: 'reconnectez-vous' }).getAttribute('href') === null, 'lien //hôte : sans adresse');
  check(!(await a.locator('body').innerText()).includes('Sommaire piégé'), 'faux titre absent du sommaire');

  // --- 5. PERF-9 : connexion refusée (4429), raison affichée, nouvel essai ------------------------------------
  const busyDoc = await doc('Fiche occupée');
  const otherDoc = await doc('Autre fiche');
  const raw = await ctx.newPage();
  await raw.goto(`${BASE}/api/health`);   // même origine, sans l'interface (qui ouvrirait sa propre connexion)
  // 12 connexions temps réel du même compte (MAZE_LIVE_MAX_PER_USER par défaut) sur une autre fiche.
  const opened = await raw.evaluate(async ({ id, v }) => {
    window.socks = [];
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    for (let i = 0; i < 12; i += 1) {
      const ws = new WebSocket(`${proto}//${window.location.host}/api/documents/${id}/live?client=${900000 + i}&v=${v}`);
      window.socks.push(ws);
      await new Promise((resolve) => {
        ws.onmessage = (e) => { if (typeof e.data === 'string' && JSON.parse(e.data).t === 'welcome') resolve(); };
        ws.onclose = () => resolve();
      });
    }
    return window.socks.filter((s) => s.readyState === WebSocket.OPEN).length;
  }, { id: otherDoc, v: SCHEMA });
  check(opened === 12, '12 connexions temps réel ouvertes par le même compte');
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${busyDoc}`);
  const saveState = a.locator('.save-state');
  check(await until(async () => (await saveState.innerText()).includes(BUSY), 15000),
    `indicateur de la fiche : « ${await saveState.innerText()} »`);
  check((await a.locator('.doc-connecting').innerText()).includes(`${BUSY} : nouvel essai en cours`),
    'zone de la fiche : raison du refus et nouvel essai en cours');
  await raw.evaluate(() => window.socks.pop().close());
  check(await until(async () => (await saveState.innerText()) === 'Enregistré', 25000)
    && await a.locator('.doc .ProseMirror').isVisible(), 'une place libérée : la fiche se connecte au nouvel essai');
  await raw.evaluate(() => window.socks.forEach((s) => s.close()));
  await raw.close();

  // --- 6. FRONT-7 : liaison lente, journal reçu en plus de 10 s ------------------------------------------------
  const slowDoc = await doc('Fiche volumineuse');
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${slowDoc}`);
  await a.locator('.doc .ProseMirror').click();
  const typed = 'Journal long : chaque lettre est une trame.';
  for (const ch of typed) { await a.keyboard.type(ch); await sleep(110); }   // > 60 ms : une modification par lettre
  check(await until(async () => (await saveState.innerText()) === 'Enregistré', 10000), 'texte enregistré lettre par lettre');
  await a.goto(`${BASE}/#/c/${cid}`);
  const slow = await ctx.newPage();
  slow.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  const link = { connections: 0, frames: 0, firstWelcome: null, start: 0 };
  await slow.routeWebSocket(/\/api\/documents\/\d+\/live/, (ws) => {
    link.connections += 1;
    const server = ws.connectToServer();
    let chain = Promise.resolve();
    server.onMessage((message) => {
      // Liaison lente : chaque trame du serveur arrive 350 ms après la précédente (le journal compte une trame par
      // lettre tapée, puis le « welcome »).
      chain = chain.then(() => sleep(350)).then(() => {
        link.frames += 1;
        if (typeof message === 'string' && message.includes('"welcome"') && link.firstWelcome === null) {
          link.firstWelcome = Date.now() - link.start;
        }
        return ws.send(message);
      }).catch(() => {});
    });
    ws.onClose(() => server.close().catch(() => {}));
    server.onClose(() => ws.close().catch(() => {}));
  });
  link.start = Date.now();
  await slow.goto(`${BASE}/#/c/${cid}/scenarios/${slowDoc}`);
  const ready = await until(async () => (await slow.locator('.doc .ProseMirror').innerText()).includes(typed), 45000);
  check(link.firstWelcome !== null && link.firstWelcome > 10000,
    `journal reçu en ${(link.firstWelcome / 1000).toFixed(1)} s (${link.frames} trames) : plus que le délai de 10 s`);
  check(ready && link.connections === 1, `fiche ouverte sur la liaison lente, une seule connexion (${link.connections})`);
  await slow.close();

  // --- 7. PERF-15 : Administration > Sauvegardes, images d'avant une restauration interrompue -------------------
  const leftover = '.media-remplace-1760000000';
  let restoreMedia = null;
  let interrupted = null;
  if (DATA) {
    mkdirSync(path.join(DATA, leftover, '1'), { recursive: true });
    writeFileSync(path.join(DATA, leftover, '1', 'a.webp'), Buffer.alloc(2048, 1));
    // media/ avec au moins un fichier : vide, il compte comme absent (Maze le recrée vide au démarrage).
    mkdirSync(path.join(DATA, 'media', '1'), { recursive: true });
    writeFileSync(path.join(DATA, 'media', '1', `e2e-${TAG}.webp`), Buffer.alloc(16, 1));
  } else {
    // Sans accès au dossier de données : réponse du serveur complétée dans le navigateur.
    await a.route('**/api/admin/backups/usage', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.replaced_media = restoreMedia === 'fini' ? [] : [{ name: leftover, files: 1, size: 2048,
        modified: '2025-10-09T08:53:20Z', media_missing: Boolean(restoreMedia) }];
      body.interrupted_restores = interrupted ? [{ name: interrupted, files: 1, size: 64, modified: '2025-10-09T08:53:20Z',
        media_missing: false }] : [];
      await route.fulfill({ response: res, json: body });
    });
  }
  const usage = async () => (await api(a, 'GET', '/api/admin/backups/usage')).replaced_media;
  if (DATA) check((await usage()).some((m) => m.name === leftover && m.files === 1 && !m.media_missing), 'API : replaced_media signalé');
  await a.goto(`${BASE}/#/admin/sauvegardes`);
  const notice = a.locator(`[data-replaced-media="${leftover}"]`);
  await notice.waitFor({ timeout: 15000 });
  let text = await notice.innerText();
  check(await notice.evaluate((el) => el.classList.contains('notice--warning')) && await notice.getAttribute('role') === null
    && text.includes(`/data/${leftover}`) && text.includes(`docker compose exec maze rm -r /data/${leftover}`)
    && text.includes('docs/fr/guide.md'), 'alerte : dossier, nombre de fichiers, commande de suppression, renvoi au guide');
  // media/ absent : alerte plus forte, remise en place d'abord.
  if (DATA) {
    restoreMedia = path.join(DATA, `media-e2e-${TAG}`);
    renameSync(path.join(DATA, 'media'), restoreMedia);
    check((await usage()).some((m) => m.name === leftover && m.media_missing), 'API : media_missing quand media/ manque');
  } else {
    restoreMedia = 'simulé';
  }
  await a.reload();
  await notice.waitFor({ timeout: 15000 });
  text = await notice.innerText();
  check(await notice.evaluate((el) => el.classList.contains('notice--danger')) && await notice.getAttribute('role') === 'alert'
    && text.includes('Images de Maze absentes')
    && text.includes(`find /data/media -depth -type d -empty -delete; test ! -e /data/media && mv /data/${leftover} /data/media`),
  'media/ absent : alerte forte (role=alert), commande de remise en place');
  await a.screenshot({ path: path.join(SHOTS, 'finitions-sauvegardes.png') });
  if (DATA) {
    renameSync(restoreMedia, path.join(DATA, 'media'));
    rmSync(path.join(DATA, leftover), { recursive: true, force: true });
  } else {
    restoreMedia = 'fini';
  }

  // Images d'une restauration interrompue, gardées au démarrage faute de base restaurée (contre-revue du lot 6).
  interrupted = `.restauration-e2e${TAG}`;
  if (DATA) {
    mkdirSync(path.join(DATA, interrupted, 'media', '1'), { recursive: true });
    writeFileSync(path.join(DATA, interrupted, 'media', '1', 'a.webp'), Buffer.alloc(64, 1));
    check((await api(a, 'GET', '/api/admin/backups/usage')).interrupted_restores
      .some((m) => m.name === interrupted && m.files === 1 && !m.media_missing), 'API : interrupted_restores signalé');
  }
  await a.reload();
  const keptNotice = a.locator(`[data-interrupted-restore="${interrupted}"]`);
  await keptNotice.waitFor({ timeout: 15000 });
  text = await keptNotice.innerText();
  check(text.includes('Restauration interrompue') && text.includes(`/data/${interrupted}/media`)
    && text.includes('pas celle de cette sauvegarde') && text.includes(`docker compose exec maze rm -r /data/${interrupted}`)
    && await keptNotice.evaluate((el) => el.classList.contains('notice--warning')),
  'alerte « Restauration interrompue » : dossier gardé, base différente, commande de suppression');
  if (DATA) {
    rmSync(path.join(DATA, interrupted), { recursive: true, force: true });
    rmSync(path.join(DATA, 'media', '1', `e2e-${TAG}.webp`), { force: true });
  } else {
    await a.unrouteAll({ behavior: 'ignoreErrors' });
  }
  await a.reload();
  await a.getByRole('heading', { name: 'Sauvegardes', exact: true }).first().waitFor({ timeout: 15000 });
  await sleep(800);
  check(await a.locator('[data-replaced-media], [data-interrupted-restore]').count() === 0, 'dossiers supprimés : plus d’alerte');

  // --- 8. Lien ouvert juste après la connexion, pendant l'arrivée de la liste des campagnes (contre-revue) --------
  // L'adresse change avant que hashchange soit traité (simulé : replaceState, puis l'événement après la liste) ;
  // la liste arrivée entre-temps ne doit pas remplacer l'adresse par celle de la campagne (replaceState).
  const late = await (await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' })).newPage();
  let release;
  const held = new Promise((r) => { release = r; });
  let asked = false;
  await late.route((url) => url.pathname === '/api/campaigns', async (route) => {
    if (route.request().method() === 'GET' && !asked) { asked = true; await held; }
    await route.continue();
  });
  await login(late);
  check(await until(async () => asked, 8000), 'connexion : liste des campagnes demandée (réponse retenue)');
  const deep = `#/c/${cid}/scenarios/${fiche}`;
  await late.evaluate((h) => window.history.replaceState(window.history.state, '', h), deep);
  release();
  await late.locator('.module-tabs').waitFor({ timeout: 15000 });
  await sleep(500);
  const kept = await late.evaluate(() => window.location.hash);
  check(kept === deep, `liste des campagnes arrivée : adresse gardée (${kept})`);
  await late.evaluate(() => window.dispatchEvent(new HashChangeEvent('hashchange')));
  await late.locator('.doc .ProseMirror').waitFor({ timeout: 15000 });
  check(await until(async () => (await late.locator('.doc .ProseMirror').innerText()).includes('Contenu du futur modèle.'), 8000),
    'hashchange traité : fiche du lien ouverte');
  await late.context().close();

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`FINITIONS (INTERFACE) OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`);
} catch (err) {
  console.error(`ÉCHEC : ${err.message}`);
  if (errors.length) console.error(`Erreurs de page : ${errors.join(' | ')}`);
  if (page) await page.screenshot({ path: path.join(SHOTS, 'finitions-echec.png') }).catch(() => {});
  if (DATA && existsSync(path.join(DATA, `media-e2e-${TAG}`)) && !existsSync(path.join(DATA, 'media'))) {
    renameSync(path.join(DATA, `media-e2e-${TAG}`), path.join(DATA, 'media'));
  }
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
