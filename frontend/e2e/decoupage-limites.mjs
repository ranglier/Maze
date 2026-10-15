// Parcours navigateur des cas limites des onglets Scénarios et Lieux > Carte interactive, ajouté par la contre-revue
// du découpage de ScenariosView.jsx et MapsView.jsx (audit QUAL-10) : ce que `scenarios` et `cartes-interactives` ne
// voient pas (une fiche ou une carte remplace la précédente pendant un chargement lent, titre envoyé pour la bonne
// fiche au changement de fiche, deux onglets sur la même fiche, restauration d'une version, téléphone ; pleine
// définition d'un grand fond au fort zoom, anneau au bord qui fait glisser la carte, recentrage à l'arrivée par
// ?place=, pincement et fiche du lieu en feuille basse sur téléphone). Joué sur le code d'avant le découpage et
// après : mêmes résultats.
//
// Base vide ou déjà initialisée. Usage : node decoupage-limites.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { deflateSync } from 'node:zlib';

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
const PHONE = { viewport: { width: 412, height: 839 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'fr-FR' };

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
  // Interface arrivée sur sa page de départ (campagne, ou accueil sans campagne) : un page.goto('#/…') lancé avant
  // était parfois écrasé par cette redirection (onglet resté sur la fiche de campagne, intégration continue).
  await page.waitForFunction(() => /^#\/c\/\d+/.test(window.location.hash)
    || [...document.querySelectorAll('button')].some((x) => x.textContent.includes('Créer une campagne')), null,
  { timeout: 15000 });
}

/** PNG en couleurs unies par bandes (w × h), sans dépendance : fond de carte importé. */
function png(w, h) {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4); sum.writeUInt32BE(crc(body));
    return Buffer.concat([len, body, sum]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y += 1) {
    const row = y * (w * 3 + 1);
    for (let x = 0; x < w; x += 1) {
      const band = Math.floor(x / 100) % 2;
      raw[row + 1 + x * 3] = band ? 70 : 40;
      raw[row + 2 + x * 3] = 90 + Math.floor((y / h) * 60);
      raw[row + 3 + x * 3] = band ? 120 : 80;
    }
  }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

/** Vue de la carte affichée : translate(x, y) scale(k) de l'image de fond. */
async function viewOf(page) {
  const t = await page.locator('.map-view__image').getAttribute('style');
  const m = t.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/);
  return { x: Number(m[1]), y: Number(m[2]), k: Number(m[3]) };
}

async function mapReady(page) {
  await page.locator('.map-view__image').waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelector('.map-view__image')?.complete
    && document.querySelector('.map-view__image').naturalWidth > 0, null, { timeout: 30000 });
  await page.locator('.map-view__overlay').waitFor({ timeout: 10000 });
  await sleep(400);
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctx.newPage();
  await login(a);

  // --- Scénarios ------------------------------------------------------------------------------------------
  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Limites ${TAG}` })).id;
  const root = (await api(a, 'GET', `/api/campaigns/${cid}/documents`)).root_id;
  const A = await api(a, 'POST', `/api/campaigns/${cid}/documents`, { parent_id: root, title: 'Alpha', content: '<p>Texte alpha</p>' });
  const B = await api(a, 'POST', `/api/campaigns/${cid}/documents`, { parent_id: root, title: 'Beta', content: '<p>Texte beta</p>' });
  await api(a, 'POST', `/api/documents/${A.id}/comments`, { body: 'Fil sur alpha', quote: '' });
  await api(a, 'POST', `/api/documents/${A.id}/versions`, { label: 'Version alpha', content: '<p>Ancien alpha</p>' });
  const tree = a.locator('aside.doc-tree');
  const item = (t) => tree.locator('.doc-tree__link', { has: a.locator('.doc-tree__label', { hasText: new RegExp(`^${t.replace(/[()]/g, '\\$&')}$`) }) });
  const editor = a.locator('.doc .ProseMirror');
  const title = a.locator('textarea.doc__title');
  const side = a.locator('.doc-side');
  const badge = a.locator('.doc-side__tabs .doc-side__badge');
  const opened = (t, text) => until(async () => (await title.inputValue()) === t && (await editor.innerText()).includes(text), 15000);
  await a.goto(`${BASE}/#/c/${cid}/scenarios/${B.id}`);
  await a.reload();
  check(await opened('Beta', 'beta'), 'fiche Beta ouverte');
  await a.locator('.doc-side__tabs [role=tab]', { hasText: 'Historique' }).click();
  await sleep(500);

  // 1. Alpha demandée (fiche, commentaires et historique lents), puis Beta aussitôt : rien d'Alpha ne s'affiche.
  const slow = (ms) => async (route) => { await sleep(ms); await route.continue().catch(() => {}); };
  await a.route(`**/api/documents/${A.id}`, (r) => (r.request().method() === 'GET' ? slow(1500)(r) : r.continue()));
  await a.route(`**/api/documents/${A.id}/comments`, slow(2500));
  await a.route(`**/api/documents/${A.id}/versions`, slow(2500));
  await item('Alpha').click();
  await sleep(150);
  await item('Beta').click();
  await sleep(3500);
  check(a.url().endsWith(`/scenarios/${B.id}`) && await title.inputValue() === 'Beta'
    && (await editor.innerText()).includes('beta'), 'Alpha puis Beta pendant le chargement : Beta affichée');
  check(await badge.count() === 0 && !(await side.innerText()).includes('Version alpha'),
    'commentaires et historique d’Alpha arrivés en retard : ignorés');
  await a.unrouteAll({ behavior: 'ignoreErrors' });
  await a.route(`**/api/documents/${A.id}/versions`, slow(1200));
  await item('Alpha').click();
  check(await opened('Alpha', 'alpha'), 'Alpha ouverte (historique lent)');
  check(await until(async () => (await side.innerText()).includes('Version alpha'), 6000)
    && await badge.innerText() === '1', 'Alpha : son historique et son fil de commentaires');
  await a.unrouteAll({ behavior: 'ignoreErrors' });

  // 2. Titre tapé puis autre fiche aussitôt (avant l'envoi différé) : enregistré pour la fiche où il a été tapé.
  await item('Beta').click();
  check(await opened('Beta', 'beta'), 'Beta ouverte');
  await sleep(300);
  await title.fill('Beta renommée');
  await item('Alpha').click();
  check(await until(async () => (await api(a, 'GET', `/api/documents/${B.id}`)).title === 'Beta renommée', 5000)
    && (await api(a, 'GET', `/api/documents/${A.id}`)).title === 'Alpha', 'titre envoyé pour Beta, Alpha inchangée');

  // 3. Deux onglets sur la même fiche : présence, titre, statut et texte de l'autre ; son propre titre en cours de frappe.
  check(await opened('Alpha', 'alpha'), 'Alpha ouverte');
  const ctx2 = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const b = await ctx2.newPage();
  await login(b);
  await b.goto(`${BASE}/#/c/${cid}/scenarios/${A.id}`);
  await b.locator('.doc .ProseMirror').waitFor({ timeout: 15000 });
  check(await until(async () => (await a.locator('.presence__dot').count()) === 1, 8000), 'présence de l’autre onglet');
  await b.locator('textarea.doc__title').fill('Alpha (par B)');
  await b.locator('.status-pick__opt.status--joue').click();
  check(await until(async () => (await title.inputValue()) === 'Alpha (par B)', 8000), 'titre changé par l’autre : repris');
  check(await until(async () => (await item('Alpha (par B)').count()) === 1, 5000), 'titre changé par l’autre : repris dans l’arbre');
  check(await until(async () => (await a.locator('.status-pick__opt.status--joue.is-active').count()) === 1, 5000),
    'statut changé par l’autre : repris');
  await b.locator('.doc .ProseMirror').click();
  await b.keyboard.press('End');
  await b.keyboard.type(' ajout de B');
  check(await until(async () => (await editor.innerText()).includes('ajout de B'), 8000), 'texte tapé par l’autre : reçu');
  await title.focus();
  await title.fill('Alpha (par A)');
  await b.locator('textarea.doc__title').fill('Alpha (par B, encore)');
  await sleep(400);
  check(await title.inputValue() === 'Alpha (par A)', 'titre en cours de frappe : pas écrasé par celui de l’autre');
  await editor.click();
  await sleep(1500);
  await ctx2.close();

  // 4. Restauration d'une version : contenu remplacé, fin annoncée par le temps réel.
  await a.locator('.doc-side__tabs [role=tab]', { hasText: 'Historique' }).click();
  await a.locator('.history__open', { hasText: 'Version alpha' }).click();
  await a.getByRole('button', { name: 'Restaurer cette version' }).first().click();
  await a.locator('.modal').getByRole('button', { name: 'Restaurer' }).click();
  const toast = await until(async () => (await a.locator('.toast').allInnerTexts()).find((x) => /restaurée|remplacée/.test(x)), 8000);
  check(toast === 'Version restaurée.', `restauration annoncée (« ${toast} »)`);
  check(await until(async () => (await editor.innerText()).includes('Ancien alpha'), 8000), 'restauration : texte de la version');

  // 5. Téléphone : panneau latéral en surimpression, menu ⋯ > Sommaire.
  const phone = await browser.newContext(PHONE);
  const p = await phone.newPage();
  await login(p);
  await p.goto(`${BASE}/#/c/${cid}/scenarios/${B.id}`);
  await p.locator('.doc .ProseMirror').waitFor({ timeout: 15000 });
  check(await p.locator('.doc-side.is-open').count() === 0, 'téléphone : panneau latéral replié');
  await p.locator('.doc__comments-btn').click();
  await p.locator('.doc-side.is-open').waitFor({ timeout: 5000 });
  check(await p.locator('.doc-side__tabs [aria-selected=true]').innerText() === 'Commentaires', 'téléphone : commentaires en surimpression');
  await p.locator('.doc-side__close').click();
  await p.locator('.doc-side.is-open').waitFor({ state: 'detached', timeout: 5000 });
  await p.locator('.doc__more').click();
  await p.getByRole('menuitem', { name: 'Sommaire' }).click();
  await p.locator('.doc-side.is-open').waitFor({ timeout: 5000 });
  check(await p.locator('.doc-side__tabs [aria-selected=true]').innerText() === 'Sommaire', 'téléphone : menu ⋯ > Sommaire');
  await phone.close();

  // --- Carte interactive ------------------------------------------------------------------------------------
  const mcid = (await api(a, 'POST', '/api/campaigns', { name: `Limites cartes ${TAG}` })).id;
  const mk = async (name, parent = null) => (await api(a, 'POST', `/api/campaigns/${mcid}/places`,
    { name, kind: 'lieu', ...(parent ? { parent_id: parent } : {}) })).id;
  const est = await mk('Est');
  for (let i = 1; i <= 6; i += 1) await mk(`Quartier est ${i}`, est);
  const ouest = await mk('Ouest');
  const sous = await mk('Sous-ouest', ouest);
  await mk('Cave ouest', sous);
  const upload = async (name, w, h) => {
    const res = await a.request.post(`${BASE}/api/campaigns/${mcid}/maps`,
      { multipart: { name, file: { name: `${name}.png`, mimeType: 'image/png', buffer: png(w, h) } } });
    if (!res.ok()) throw new Error(`import du fond ${name} : ${res.status()} ${await res.text()}`);
    return api(a, 'GET', `/api/maps/${(await res.json()).id}`);
  };
  const big = await upload('Grande', 4800, 2400);   // plus de 4096 px : version d'affichage, puis pleine définition
  const small = await upload('Petite', 1200, 800);
  check(Boolean(big.preview_url) && !small.preview_url, 'grand fond : version d’affichage créée');
  await api(a, 'PUT', `/api/maps/${big.id}/pins/${est}`, { x: 0.98, y: 0.5 });
  await api(a, 'PUT', `/api/maps/${big.id}/pins/${ouest}`, { x: 0.3, y: 0.3 });
  await api(a, 'PUT', `/api/maps/${small.id}/pins/${ouest}`, { x: 0.5, y: 0.5 });
  const pin = (page, name) => page.locator('.map-pin', { has: page.locator('.map-pin__label', { hasText: new RegExp(`^${name}$`) }) });

  // 6. Grande carte demandée (lente), puis la petite aussitôt : la petite s'affiche. Dans un nouvel onglet (celui des
  // scénarios peut retenir un rechargement).
  await ctx.close();
  const mctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const m = await mctx.newPage();
  await login(m);
  await m.goto(`${BASE}/#/c/${mcid}/cartes/${small.id}`);
  await mapReady(m);
  const image = m.locator('.map-view__image');
  await m.route(`**/api/maps/${big.id}`, slow(1500));
  await m.locator('select.maps__select').selectOption(String(big.id));
  await sleep(200);
  await m.locator('select.maps__select').selectOption(String(small.id));
  await sleep(2500);
  check(await m.locator('.map-view').getAttribute('aria-label') === 'Carte Petite', 'carte demandée en dernier affichée');
  await m.unrouteAll({ behavior: 'ignoreErrors' });

  // 7. Grande carte : version d'affichage, puis pleine définition au fort zoom (molette).
  await m.locator('select.maps__select').selectOption(String(big.id));
  await mapReady(m);
  check(await image.getAttribute('src') === big.preview_url, 'grande carte : version d’affichage d’abord');
  const fit = await viewOf(m);
  const vbox = await m.locator('.map-view').boundingBox();
  await m.mouse.move(vbox.x + vbox.width * 0.4, vbox.y + vbox.height * 0.4);
  for (let i = 0; i < 12; i += 1) { await m.mouse.wheel(0, -400); await sleep(60); }
  check(await until(async () => (await image.getAttribute('src')) === big.url, 15000), 'fort zoom : pleine définition chargée');

  // 8. Anneau d'une épingle au bord droit : la carte glisse pour le montrer, à gauche de la fiche du lieu.
  await m.getByRole('button', { name: 'Toute la carte' }).click();
  await sleep(500);
  const before = await viewOf(m);
  await pin(m, 'Est').click({ force: true });
  await m.locator('.map-chip').first().waitFor({ timeout: 5000 });
  await sleep(800);
  const after = await viewOf(m);
  const right = await m.locator('.map-chip').evaluateAll((els) => Math.max(...els.map((e) => e.getBoundingClientRect().right)));
  check(after.x < before.x - 100 && right <= 1400 - 354, `anneau au bord : la carte glisse de ${Math.round(after.x - before.x)} px`);
  await m.keyboard.press('Escape');

  // 9. Arrivée par ?place= : bonne carte, anneaux ouverts jusqu'au lieu, recentrage avec zoom, depuis la vue
  // d'ensemble (épingle à l'écran ; hors de l'écran : 9 bis).
  await m.getByRole('button', { name: 'Toute la carte' }).click();
  await sleep(500);
  await m.goto(`${BASE}/#/c/${mcid}/cartes?place=${sous}`);
  await m.locator('.map-panel .map-panel__title', { hasText: 'Sous-ouest' }).waitFor({ timeout: 15000 });
  await sleep(1200);
  const centered = await viewOf(m);
  check(await m.locator('.map-view').getAttribute('aria-label') === 'Carte Grande'
    && await m.locator('svg.map-rings g').count() === 2, '?place= : grande carte, deux anneaux ouverts');
  check(centered.k > fit.k * 1.2, `?place= : recentrage avec zoom (échelle ${fit.k.toFixed(3)} > ${centered.k.toFixed(3)})`);

  // 9 bis. Même arrivée (lien ?place= dans l'onglet déjà ouvert) avec l'épingle et ses anneaux hors de l'écran (vue
  // d'ensemble glissée vers la gauche) : le glissement qui montre l'anneau ne part plus et n'interrompt plus le
  // recentrage, qui garde son zoom (info du lot 5, corrigé au lot 6).
  await m.keyboard.press('Escape');
  await m.keyboard.press('Escape');
  await m.getByRole('button', { name: 'Toute la carte' }).click();
  await sleep(500);
  const mb = await m.locator('.map-view').boundingBox();
  await m.mouse.move(mb.x + mb.width * 0.45, mb.y + mb.height * 0.6);
  await m.mouse.down();
  for (let i = 1; i <= 12; i += 1) { await m.mouse.move(mb.x + mb.width * 0.45 - i * 60, mb.y + mb.height * 0.6 - i * 25); await sleep(16); }
  await m.mouse.up();
  await sleep(300);
  const shifted = await viewOf(m);
  const ouestX = shifted.x + 0.3 * 4800 * shifted.k;
  check(ouestX < 0 && shifted.k < fit.k * 1.1, `épingle « Ouest » hors de l'écran (x = ${Math.round(ouestX)} px), vue d'ensemble`);
  await m.evaluate((h) => { window.location.hash = h; }, `#/c/${mcid}/cartes/${big.id}?place=${sous}`);
  await m.locator('.map-panel .map-panel__title', { hasText: 'Sous-ouest' }).waitFor({ timeout: 15000 });
  await sleep(1200);
  const refocused = await viewOf(m);
  const ox = refocused.x + 0.3 * 4800 * refocused.k;
  const oy = refocused.y + 0.3 * 2400 * refocused.k;
  check(await m.locator('svg.map-rings g').count() === 2, '?place=, épingle hors de l\'écran : deux anneaux ouverts');
  check(refocused.k > fit.k * 1.2 && ox > 0 && ox < mb.width - 354 && oy > 0 && oy < mb.height,
    `?place=, épingle hors de l'écran : recentrage avec zoom (échelle ${refocused.k.toFixed(3)}, épingle à ${Math.round(ox)}, ${Math.round(oy)})`);

  // 10. Téléphone : pincement (vrais événements tactiles), fiche du lieu en feuille basse.
  const phone2 = await browser.newContext(PHONE);
  const q = await phone2.newPage();
  await login(q);
  await q.goto(`${BASE}/#/c/${mcid}/cartes/${big.id}`);
  await mapReady(q);
  const q0 = await viewOf(q);
  const cdp = await phone2.newCDPSession(q);
  const qb = await q.locator('.map-view').boundingBox();
  const cx = qb.x + qb.width / 2;
  const cy = qb.y + qb.height / 2;
  const touch = (type, d) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? []
    : [{ x: cx - 20 - d, y: cy, id: 1 }, { x: cx + 20 + d, y: cy, id: 2 }] });
  await touch('touchStart', 0);
  for (let i = 1; i <= 10; i += 1) { await touch('touchMove', i * 8); await sleep(16); }
  await touch('touchEnd', 0);
  await sleep(400);
  const q1 = await viewOf(q);
  check(q1.k / q0.k > 1.5, `téléphone : pincement (échelle × ${(q1.k / q0.k).toFixed(2)})`);
  await q.getByRole('button', { name: 'Toute la carte' }).click();
  await sleep(500);
  await pin(q, 'Ouest').tap({ force: true });
  await q.locator('.map-panel').waitFor({ timeout: 5000 });
  await sleep(800);
  const panel = await q.locator('.map-panel').boundingBox();
  check(panel.y > 839 / 2 && panel.width > 400, 'téléphone : fiche du lieu en feuille basse');
  await phone2.close();

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`CAS LIMITES OK (compte ${USER}, mot de passe généré : ${PASSWORD})`);
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const page of browser.contexts().flatMap((c) => c.pages())) {
    await page.screenshot({ path: path.join(SHOTS, `limites-echec-${Date.now()}.png`) }).catch(() => {});
  }
  if (errors.length) console.error(errors.join('\n'));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
