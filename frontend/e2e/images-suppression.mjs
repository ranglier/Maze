// Parcours navigateur d'Administration > Images (ImagesSection.jsx) : tri par nom gardé dans le navigateur,
// sélection multiple (case de la vignette, Maj+clic pour une plage, Ctrl+clic, Ctrl+A dans la grille, Échap),
// barre d'actions (compteur, taille, images masquées par le filtre, « Tout sélectionner »), suppression groupée
// (récapitulatif de la confirmation par sorte et conséquences, toast, images restantes), puis mode « Sélectionner »
// au toucher sur téléphone.
//
// Base vide ou déjà initialisée. Usage : node images-suppression.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

/** PNG uni à deux bandes (w × h), teinte choisie par `hue` (0 à 5), sans dépendance. */
function png(w, h, hue = 0) {
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
  const colors = [[150, 70, 60], [70, 120, 90], [60, 90, 150], [160, 130, 60], [110, 70, 140], [60, 130, 140]];
  const [r, g, b] = colors[hue % colors.length];
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y += 1) {
    const row = y * (w * 3 + 1);
    const k = y < h / 2 ? 1 : 0.6;
    for (let x = 0; x < w; x += 1) {
      raw[row + 1 + x * 3] = r * k; raw[row + 2 + x * 3] = g * k; raw[row + 3 + x * 3] = b * k;
    }
  }
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

async function api(page, method, url, data) {
  const res = await page.request.fetch(BASE + url, { method, data });
  if (!res.ok()) throw new Error(`${method} ${url} : ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

async function upload(page, url, name, hue) {
  const res = await page.request.post(BASE + url,
    { multipart: { files: { name, mimeType: 'image/png', buffer: png(320, 240, hue) } } });
  if (!res.ok()) throw new Error(`envoi de ${name} : ${res.status()} ${await res.text()}`);
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctx.newPage();
  a.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  a.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
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

  // Données : un PNJ avec une image de galerie (devenue son portrait), six images en bibliothèque.
  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Images ${TAG}` })).id;
  const hart = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Hart' });
  await upload(a, `/api/npcs/${hart.id}/images`, 'portrait-hart.png', 5);
  for (let i = 1; i <= 6; i += 1) await upload(a, `/api/admin/campaigns/${cid}/library`, `img-${i}.png`, i - 1);
  check((await api(a, 'GET', `/api/npcs/${hart.id}`)).portrait_image_id, 'image de galerie devenue le portrait de Hart');

  const grid = a.locator('.images-admin__grid');
  const card = (name) => a.locator('.images-admin__grid > li', { has: a.locator('.images-admin__title', { hasText: name }) });
  const box = (name) => card(name).locator('.images-admin__pick input');
  const bar = a.locator('.images-admin__actions');
  const summary = a.locator('.images-admin__summary');
  const titles = async () => grid.locator('.images-admin__title').allTextContents();
  const pressed = async () => grid.locator('.images-admin__card[aria-pressed="true"]').count();

  await a.goto(`${BASE}/#/admin/images`);
  await a.locator('select.images-admin__campaign').selectOption(String(cid));
  await card('img-6.png').waitFor({ timeout: 15000 });

  // 1. Tri par nom, gardé dans le navigateur.
  await a.locator('select.images-admin__sort').selectOption('name');
  check((await titles()).join() === 'img-1.png,img-2.png,img-3.png,img-4.png,img-5.png,img-6.png,portrait-hart.png',
    'tri « Nom » : ordre alphabétique des titres');
  await a.reload();
  await a.locator('select.images-admin__campaign').selectOption(String(cid));
  await card('img-6.png').waitFor({ timeout: 15000 });
  check(await a.locator('select.images-admin__sort').inputValue() === 'name', 'tri gardé après rechargement');

  // 2. Ctrl+A dans la grille, puis Échap vide la sélection.
  await card('img-1.png').locator('.images-admin__card').focus();
  await a.keyboard.press('Control+a');
  await bar.waitFor({ timeout: 5000 });
  check((await summary.textContent()).includes('7 images sélectionnées'), 'Ctrl+A dans la grille : 7 images sélectionnées');
  await a.keyboard.press('Escape');
  await bar.waitFor({ state: 'detached', timeout: 5000 });
  check(await pressed() === 0, 'Échap : sélection vidée, barre retirée');

  // 3. Deux cases cochées, sans ouvrir la fenêtre de l'image.
  await card('img-1.png').hover();
  await box('img-1.png').click();
  await card('img-2.png').hover();
  await box('img-2.png').click();
  check(await a.locator('.modal').count() === 0, 'clic sur la case : pas de fenêtre ouverte');
  check((await summary.textContent()).includes('2 images sélectionnées'), 'deux cases cochées : compteur à 2');
  check(await card('img-1.png').locator('.images-admin__card').getAttribute('aria-pressed') === 'true',
    'vignette sélectionnée : aria-pressed');

  // 4. Maj+clic : plage depuis la dernière vignette cliquée (img-2) jusqu'à img-5.
  await card('img-5.png').locator('.images-admin__card').click({ modifiers: ['Shift'] });
  check((await summary.textContent()).includes('5 images sélectionnées'), 'Maj+clic : plage img-1 à img-5, 5 images');
  // Sélection en cours : un clic simple bascule (pas de fenêtre), Ctrl+clic aussi.
  await card('img-5.png').locator('.images-admin__card').click();
  check(await a.locator('.modal').count() === 0 && (await summary.textContent()).includes('4 images sélectionnées'),
    'sélection en cours : clic simple sur une vignette = bascule');
  await card('img-5.png').locator('.images-admin__card').click({ modifiers: ['Control'] });
  await card('portrait-hart.png').locator('.images-admin__card').click({ modifiers: ['Control'] });
  check((await summary.textContent()).includes('6 images sélectionnées'), 'Ctrl+clic : 6 images (dont le portrait)');

  // 5. Filtre « Bibliothèque » : sélection gardée, image masquée signalée ; « Tout sélectionner » (images affichées).
  await a.getByRole('tab', { name: /^Bibliothèque/ }).click();
  await card('portrait-hart.png').waitFor({ state: 'detached', timeout: 5000 });
  check((await summary.textContent()).includes('dont 1 masquée par le filtre'), 'filtre : 1 image sélectionnée masquée');
  await bar.getByRole('button', { name: 'Tout sélectionner (6)' }).click();
  check((await summary.textContent()).includes('7 images sélectionnées'), 'Tout sélectionner : 7 images (6 + la masquée)');
  await card('img-6.png').locator('.images-admin__card').click();   // img-6 gardée
  check((await summary.textContent()).includes('6 images sélectionnées'), 'img-6 retirée de la sélection');
  await sleep(200);
  await a.screenshot({ path: path.join(SHOTS, 'images-selection.png') });

  // 6. Suppression : récapitulatif, confirmation, toast, images restantes.
  await bar.getByRole('button', { name: 'Supprimer', exact: true }).click();
  const dialog = a.locator('.modal', { hasText: 'Supprimer les images sélectionnées' });
  await dialog.waitFor({ timeout: 5000 });
  const text = await dialog.textContent();
  check(text.includes('6 images') && text.includes('seront supprimées du serveur'), 'confirmation : 6 images annoncées');
  check(text.includes('5 images de la bibliothèque') && text.includes('1 image de galerie de fiche'),
    'confirmation : nombre par sorte');
  check(text.includes('1 portrait, emblème ou illustration de fiche sera remplacé par l’image suivante de sa galerie'),
    'confirmation : portrait remplacé annoncé');
  check(text.includes('Dont 1 image masquée par le filtre'), 'confirmation : image masquée par le filtre signalée');
  await a.screenshot({ path: path.join(SHOTS, 'images-confirmation.png') });
  await dialog.getByRole('button', { name: 'Supprimer 6 images' }).click();
  await a.locator('.toast', { hasText: '6 images supprimées' }).waitFor({ timeout: 15000 });
  check(true, 'toast « 6 images supprimées »');
  await bar.waitFor({ state: 'detached', timeout: 5000 });
  await card('img-1.png').waitFor({ state: 'detached', timeout: 5000 });
  check((await titles()).join() === 'img-6.png', 'bibliothèque : seule img-6 reste affichée');
  const left = (await api(a, 'GET', `/api/admin/campaigns/${cid}/images`)).images;
  check(left.length === 1 && left[0].source === 'library' && left[0].name === 'img-6.png',
    'serveur : seule img-6 reste dans la campagne');
  check(!(await api(a, 'GET', `/api/npcs/${hart.id}`)).portrait_image_id, 'portrait de Hart retiré (galerie vide)');

  // 7. Téléphone : mode « Sélectionner » au toucher, barre visible à l'écran, « Terminer ».
  for (let i = 7; i <= 9; i += 1) await upload(a, `/api/admin/campaigns/${cid}/library`, `img-${i}.png`, i);
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'fr-FR', isMobile: true,
    hasTouch: true, storageState: await ctx.storageState() });
  const p = await phone.newPage();
  p.on('pageerror', (e) => errors.push(`pageerror (téléphone): ${e.message}`));
  await p.goto(`${BASE}/#/admin/images`);
  await p.locator('select.images-admin__campaign').selectOption(String(cid));
  const pcard = (name) => p.locator('.images-admin__grid > li', { has: p.locator('.images-admin__title', { hasText: name }) });
  await pcard('img-9.png').waitFor({ timeout: 15000 });
  check(await pcard('img-9.png').locator('.images-admin__pick').evaluate((el) => getComputedStyle(el).opacity) === '1',
    'téléphone : case visible sans survol');
  await p.getByRole('button', { name: 'Sélectionner', exact: true }).tap();
  await pcard('img-7.png').locator('.images-admin__card').tap();
  await pcard('img-8.png').locator('.images-admin__card').tap();
  check(await p.locator('.modal').count() === 0, 'mode sélection : un appui ne fait pas ouvrir la fenêtre');
  const pbar = p.locator('.images-admin__actions');
  await pbar.waitFor({ timeout: 5000 });
  check((await pbar.textContent()).includes('2 images sélectionnées'), 'téléphone : 2 images sélectionnées au toucher');
  const rect = await pbar.boundingBox();
  check(rect && rect.y >= 0 && rect.y + rect.height <= 844, 'téléphone : barre d’actions dans l’écran');
  await sleep(200);
  await p.screenshot({ path: path.join(SHOTS, 'images-telephone.png') });
  await p.getByRole('button', { name: 'Terminer', exact: true }).tap();
  await pbar.waitFor({ state: 'detached', timeout: 5000 });
  check(await p.locator('.images-admin__card[aria-pressed="true"]').count() === 0, 'Terminer : sélection vidée');
  await phone.close();

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`IMAGES SUPPRESSION OK (compte ${USER}, mot de passe généré : ${PASSWORD})`);
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const page of browser.contexts().flatMap((c) => c.pages())) {
    await page.screenshot({ path: path.join(SHOTS, `images-echec-${Date.now()}.png`) }).catch(() => {});
  }
  if (errors.length) console.error(errors.join('\n'));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
