// Parcours navigateur de la chronologie (module facultatif d'une campagne, migration 032) :
//   - désactivée par défaut : ni onglet, ni section sur les fiches ;
//   - activation par le menu de la campagne (« Activer la chronologie ») ; état vide, puis trois événements créés ;
//   - insertion au milieu (« + » entre deux événements), réordonnancement par « Déplacer vers… » et à la souris ;
//   - @mention d'un PNJ dans une description : section « Chronologie » de sa fiche, lien vers l'événement ;
//   - bascule Frise, ouverture d'une carte (panneau), flèches du clavier d'une carte à l'autre ;
//   - recherche Ctrl+K : résultat « Événements », ouverture de l'événement ;
//   - téléphone : accès par le menu de la campagne ; désactivation (confirmation) qui masque tout.
//   - panneau « Étiquettes » flottant (replié par défaut, filtre, « Tout afficher », « × ») ;
//   - seconde campagne de 15 événements : liste compacte (une ligne par événement, dépliage, Échap, rechargement),
//     « + » de la frise de même taille, mini-carte (clic, clavier, absente quand tout tient), frise compacte en
//     quinconce (étiquettes tournées, sans chevauchement), téléphone.
// Captures dans MAZE_E2E_SHOTS : liste, frise, frise sur téléphone, liste normale et compacte, étiquettes filtrées,
// frise compacte (ordinateur et téléphone), liste compacte sur téléphone.
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

// Événements variés : 8 étiquettes, titres longs, fiches citées.
const TAGS = ['Guerre', 'Cour', 'Diplomatie', 'Prophétie', 'Commerce', 'Religion', 'Exploration', 'Séance'];
const EVENT_TITLES = [
  'La fondation de Varn sur les ruines de l’ancienne citadelle des rois-sorciers', 'Le grand incendie du port',
  'Première ambassade des elfes de la Brume auprès du conseil des marchands', 'Le schisme des prêtres d’Ardan',
  'La découverte des mines de sel noir', 'Le couronnement d’Ilse la Jeune', 'Traité des Sept Rivières',
  'La comète rouge annonce le retour de l’Ombre selon les devins de la cour', 'Siège de Morne', 'La peste des marais',
  'Expédition vers les îles Brisées, dont aucun navire ne revint avant trois ans', 'Mariage des maisons Varga et Oriel',
  'La chute du pont de Lumière', 'Séance 4 : les héros arrivent à Varn', 'Révolte des guildes',
];

/** Section 10 : panneau Étiquettes, liste compacte, frise compacte en quinconce, mini-carte, téléphone. */
async function displayChecks(browser, ctx, a) {
  const camp = await api(a, 'POST', '/api/campaigns', { name: `Annales ${TAG}` });
  await api(a, 'PATCH', `/api/campaigns/${camp.id}`, { timeline_enabled: true });
  const npc = await api(a, 'POST', `/api/campaigns/${camp.id}/npcs`, { name: 'Oriel Dast' });
  for (const [i, title] of EVENT_TITLES.entries()) {
    const tags = TAGS.filter((_, k) => (i + k) % 5 === 0 || (i === 4 && k < 6)).slice(0, i === 4 ? 6 : 3);
    const description = i % 3 === 0 ? `<p>Témoin : <a class="mention" data-mention="npc:${npc.id}">Oriel Dast</a>.</p>` : '<p>Notes.</p>';
    await api(a, 'POST', `/api/campaigns/${camp.id}/events`, { title, date_label: i % 4 === 3 ? '' : `An ${1200 + i * 7}, mois des brumes`, tags, description });
  }
  const url = `${BASE}/#/c/${camp.id}/chronologie`;
  await a.evaluate(() => { localStorage.setItem('maze:timeline:mode', 'list'); localStorage.removeItem('maze:timeline:density'); localStorage.removeItem('maze:timeline:density:list'); });
  // Chargement complet (pas un simple changement d'adresse) : la liste des campagnes doit connaître la nouvelle.
  await a.goto('about:blank');
  await a.goto(url);
  await a.locator('.timeline-item').first().waitFor({ timeout: 15000 });
  check(await a.locator('.timeline-filters').count() === 0 && await a.locator('.timeline-tagbar').isVisible(),
    'plus de rangée de filtres : panneau Étiquettes flottant');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'liste-normale-etiquettes-repliees.png') });

  // Liste compacte : une ligne par événement.
  const listDensity = a.getByRole('button', { name: 'Affichage compact (une ligne par événement)' });
  await listDensity.click();
  const lines = a.locator('.timeline-line');
  await until(async () => await lines.count() === 15, 'liste compacte');
  check(await a.locator('.timeline-list .timeline-card').count() === 0, 'liste compacte : une ligne par événement, aucune carte');
  const heights = await lines.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height));
  check(heights.every((h) => h >= 34 && h <= 40), `liste compacte : lignes d’environ 36 px (${Math.min(...heights)} à ${Math.max(...heights)})`);
  check(await a.evaluate(() => localStorage.getItem('maze:timeline:density:list') === 'compact'
    && localStorage.getItem('maze:timeline:density') !== 'compact'), 'densité de la liste gardée à part de celle de la frise');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'liste-compacte.png') });
  const third = a.locator('.timeline-item').nth(2);
  await third.locator('.timeline-line__open').click();
  await third.locator('.timeline-card h3').waitFor({ timeout: 5000 });
  check(await lines.count() === 14, 'clic sur une ligne : dépliée en carte à sa place');
  await a.keyboard.press('Escape');
  await third.locator('.timeline-line').waitFor({ timeout: 5000 });
  // Le focus revient au rafraîchissement suivant (requestAnimationFrame) : l'attendre plutôt que le lire tout de suite
  // (échec intermittent en CI, PR #15).
  check(await until(() => a.evaluate(() => document.activeElement?.classList.contains('timeline-line__open')),
    'focus sur la ligne repliée', 5000).then(() => true, () => false), 'Échap : ligne repliée, focus sur la ligne');
  await a.keyboard.press('Enter');
  await third.locator('.timeline-card h3').click();
  await third.locator('.timeline-line').waitFor({ timeout: 5000 });
  check(await lines.count() === 15, 'Entrée puis second clic : dépliée puis repliée');
  await a.reload();
  await until(async () => await lines.count() === 15, 'densité après rechargement');
  check(true, 'liste compacte gardée après rechargement');

  // Panneau Étiquettes déplié avec un filtre actif.
  const tagToggle = a.locator('.timeline-tagbar .legend__toggle');
  await tagToggle.click();
  await a.locator('.timeline-tagbar .legend__item', { hasText: 'Prophétie' }).click();
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'etiquettes-filtre-actif.png') });
  check(await lines.count() < 15, 'liste compacte filtrée');
  await a.locator('.timeline-tagbar .legend__item', { hasText: 'Tout afficher' }).click();
  await tagToggle.click();

  // Frise : densité propre (normale), « + » de même taille, mini-carte.
  await a.getByRole('radio', { name: 'Frise' }).click();
  await a.locator('.frieze--normal').waitFor({ timeout: 5000 });
  check(true, 'frise : densité normale, indépendante de la liste');
  const sizes = await a.locator('.frieze__insert').evaluateAll((els) => els.map((e) => {
    const r = e.getBoundingClientRect();
    return `${Math.round(r.width)}x${Math.round(r.height)}`;
  }));
  check(new Set(sizes).size === 1, `frise : pastilles « + » de même taille (${sizes[0]}, « + » de fin compris)`);
  const map = a.locator('.frieze-map');
  await map.waitFor({ timeout: 5000 });
  check(/^événements 1 à \d+ sur 15$/.test(await map.getAttribute('aria-valuetext')), 'mini-carte : position lue (aria-valuetext)');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'frise.png') });
  const box = await map.boundingBox();
  await a.mouse.click(box.x + box.width - 3, box.y + box.height / 2);
  const atEnd = () => a.locator('.frieze').evaluate((el) => el.scrollLeft >= el.scrollWidth - el.clientWidth - 2);
  await until(atEnd, 'clic près de la fin de la mini-carte');
  check(true, 'mini-carte : un clic près de la fin fait défiler à la fin');
  await map.focus();
  await a.keyboard.press('Home');
  await until(() => a.locator('.frieze').evaluate((el) => el.scrollLeft === 0), 'Début dans la mini-carte');
  check(true, 'mini-carte au clavier : Début');

  // Frise compacte : quinconce, étiquettes tournées, sans chevauchement.
  await a.getByRole('button', { name: 'Affichage compact (date et titre)' }).click();
  await a.locator('.frieze--compact').waitFor({ timeout: 5000 });
  const geo = await a.evaluate(() => {
    const slots = [...document.querySelectorAll('.frieze__slot:not(.frieze__slot--end)')];
    const frieze = document.querySelector('.frieze').getBoundingClientRect();
    return slots.map((sl) => {
      const card = sl.querySelector('.frieze__card');
      const btn = sl.querySelector('.frieze__open');
      const dot = sl.querySelector('.frieze__dot').getBoundingClientRect();
      const r = btn.getBoundingClientRect();
      return { width: sl.getBoundingClientRect().width, transform: getComputedStyle(card).transform, h: btn.offsetHeight,
        x: dot.x + dot.width / 2, top: r.top - frieze.top, bottom: frieze.bottom - r.bottom };
    });
  });
  check(geo.every((g) => g.width < 50), `frise compacte : emplacements de ${Math.round(geo[0].width)} px`);
  check(geo.every((g) => g.transform && g.transform !== 'none'), 'frise compacte : étiquettes tournées');
  const clear = geo.slice(2).every((g, k) => g.h < Math.abs(g.x - geo[k].x) * Math.SQRT1_2);
  check(clear, `frise compacte : étiquettes voisines d’un même côté sans chevauchement (${geo[0].h} px < ${Math.round(Math.abs(geo[2].x - geo[0].x) * Math.SQRT1_2)} px)`);
  check(geo.every((g) => g.top >= -1 && g.bottom >= -1), 'frise compacte : étiquettes entières dans la hauteur de la frise');
  await settle(a);
  await a.screenshot({ path: path.join(SHOTS, 'frise-compacte.png') });
  await a.locator('.frieze__open', { hasText: 'Siège de Morne' }).click();
  await a.locator('aside.timeline-panel .timeline-detail__title', { hasText: 'Siège de Morne' }).waitFor({ timeout: 5000 });
  check(await a.locator('.frieze__slot.is-selected .frieze__title', { hasText: 'Siège de Morne' }).count() === 1,
    'frise compacte : un clic ouvre le panneau, étiquette sélectionnée');
  await a.locator('aside.timeline-panel').getByRole('button', { name: 'Fermer' }).click();
  // Peu d'événements (filtre) : tout tient, pas de mini-carte.
  await tagToggle.click();
  await a.locator('.timeline-tagbar .legend__item', { hasText: 'Séance' }).click();
  await a.locator('.frieze-map').waitFor({ state: 'detached', timeout: 5000 });
  check(true, 'mini-carte absente quand toute la frise tient à l’écran');
  await a.locator('.timeline-tagbar .legend__item', { hasText: 'Tout afficher' }).click();

  // Téléphone : liste compacte, frise compacte et mini-carte, panneau masqué sous la feuille basse.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'fr-FR', isMobile: true, hasTouch: true,
    storageState: await ctx.storageState() });
  const p = await phone.newPage();
  p.on('pageerror', (e) => errors.push(`pageerror (téléphone): ${e.message}`));
  await p.goto(url);
  await p.evaluate(() => { localStorage.setItem('maze:timeline:mode', 'list'); localStorage.setItem('maze:timeline:density:list', 'compact'); });
  await p.reload();
  await p.locator('.timeline-line').first().waitFor({ timeout: 15000 });
  const phoneLine = await p.locator('.timeline-line').first().boundingBox();
  check(phoneLine.height >= 43 && phoneLine.height <= 52, `téléphone : lignes compactes de ${Math.round(phoneLine.height)} px`);
  const dateAbove = await p.locator('.timeline-item').first().evaluate((li) =>
    li.querySelector('.timeline-line__date').getBoundingClientRect().bottom <= li.querySelector('.timeline-line__title').getBoundingClientRect().top + 1);
  check(dateAbove, 'téléphone : date au-dessus du titre');
  const tb = await p.locator('.timeline-tagbar .legend__toggle').boundingBox();
  check(tb.height >= 38 && tb.height <= 44, 'téléphone : bouton Étiquettes de 38 à 44 px');
  await settle(p);
  await p.screenshot({ path: path.join(SHOTS, 'liste-compacte-telephone.png') });
  await p.getByRole('radio', { name: 'Frise' }).click();
  const phoneDensity = p.getByRole('button', { name: 'Affichage compact (date et titre)' });
  if (await phoneDensity.getAttribute('aria-pressed') !== 'true') await phoneDensity.click();
  await p.locator('.frieze--compact').waitFor({ timeout: 5000 });
  await p.locator('.frieze-map').waitFor({ timeout: 5000 });
  const phoneSlot = await p.locator('.frieze__slot').first().evaluate((el) => el.getBoundingClientRect().width);
  check(phoneSlot <= 50, `téléphone : frise compacte (emplacements de ${Math.round(phoneSlot)} px) et mini-carte`);
  await settle(p);
  await p.screenshot({ path: path.join(SHOTS, 'frise-compacte-telephone.png') });
  await p.locator('.frieze__open').nth(1).click();
  await p.locator('aside.timeline-panel .timeline-detail__title').waitFor({ timeout: 5000 });
  check(!(await p.locator('.timeline-tagbar').isVisible()), 'téléphone : panneau Étiquettes masqué sous la feuille basse');
  await phone.close();
  await a.goto('about:blank');
  await a.goto(`${BASE}/#/c/${cid0}/chronologie`);
  await a.locator('.timeline__body').waitFor({ timeout: 15000 });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
let cid0 = null;
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
  cid0 = cid;
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
  // Panneau « Étiquettes » (en bas à gauche, comme la légende de la carte) : replié par défaut.
  const tagbar = a.locator('.timeline-tagbar');
  const tagToggle = tagbar.locator('.legend__toggle');
  check(await tagToggle.getAttribute('aria-expanded') === 'false' && await tagbar.locator('.legend__items').count() === 0,
    'panneau Étiquettes replié par défaut');
  await tagToggle.click();
  const tagItems = a.locator(`[id="${await tagToggle.getAttribute('aria-controls')}"]`);
  await tagItems.waitFor({ timeout: 5000 });
  check(await tagToggle.getAttribute('aria-expanded') === 'true', 'panneau Étiquettes déplié (aria-expanded, aria-controls)');
  const diplo = tagItems.getByRole('button', { name: 'Diplomatie' });
  check(await diplo.getAttribute('aria-pressed') === 'false' && await tagItems.getByRole('button', { name: 'Tout afficher' }).count() === 0,
    'sans filtre : pas de « Tout afficher »');
  await diplo.click();
  check(await a.locator('.timeline-item h3').count() === 1 && await diplo.getAttribute('aria-pressed') === 'true',
    'filtre par étiquette : un seul événement');
  await diplo.click();
  check(await a.locator('.timeline-item h3').count() === 4, 'second clic sur l’étiquette : filtre retiré');
  await diplo.click();
  await tagItems.getByRole('button', { name: 'Tout afficher' }).click();
  check(await a.locator('.timeline-item h3').count() === 4, '« Tout afficher » retire le filtre');
  await diplo.click();
  await tagToggle.click();
  check(await tagbar.locator('.legend__items').count() === 0 && (await tagToggle.innerText()).includes('Diplomatie'),
    'replié avec un filtre : le bouton montre l’étiquette filtrée');
  await tagbar.getByRole('button', { name: 'Retirer le filtre « Diplomatie »' }).click();
  check(await a.locator('.timeline-item h3').count() === 4 && (await tagToggle.innerText()).includes('Étiquettes'),
    '« × » : filtre retiré, panneau toujours replié');
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

  // --- 10. Affichage : liste compacte, frise compacte, mini-carte (seconde campagne, 15 événements) -------------
  await displayChecks(browser, ctx, a);

  // --- 11. Désactivation : tout est masqué, rien n'est supprimé -------------------------------------------------------
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
  for (const pg of browser.contexts().flatMap((c) => c.pages())) {
    await pg.screenshot({ path: path.join(SHOTS, `echec-${pg.viewportSize()?.width}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
if (errors.length) { console.log('Erreurs navigateur :'); errors.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `CHRONOLOGIE OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'CHRONOLOGIE ÉCHEC');
process.exit(ok && !errors.some((e) => /pageerror/.test(e)) ? 0 : 1);
