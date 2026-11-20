// Parcours navigateur de la page de dépôt d'une image demandée par Claude (#/depot/<jeton>, UploadPage.jsx) : liens
// créés par le vrai connecteur Claude (identifiants OAuth générés, autorisation, outil request_upload sur /mcp), dépôt
// par choix de fichier (galerie d'un PNJ, légende proposée par Claude), collage Ctrl+V (bibliothèque), glisser-déposer
// (fiche rédigée), suivi du ticket par l'outil, lien déjà utilisé, lien expiré (horloge du navigateur avancée), page
// sur téléphone, connexion demandée puis retour à la page, MJ sans accès à la campagne.
//
// Base vide ou déjà initialisée. Usage : node depot-image.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (captures, défaut : maze-e2e-shots dans le dossier temporaire).
// Playwright n'est pas une dépendance du projet : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { createHash, randomBytes } from 'node:crypto';
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
const CALLBACK = 'https://claude.ai/api/mcp/auth_callback';
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };

/** PNG à deux bandes (w × h), sans dépendance. */
function png(w, h, [r, g, b] = [150, 70, 60]) {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf) => { let c = 0xffffffff; for (const x of buf) c = crcTable[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
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

// --- Connecteur Claude : autorisation OAuth réelle, puis appels de l'outil request_upload -------------------------

async function connectClaude(page, cid) {
  const creds = await api(page, 'POST', '/api/admin/claude/credentials', {});
  await api(page, 'PUT', '/api/admin/claude', { enabled: true, grants: [{ campaign_id: cid, mode: 'write' }] });
  const verifier = randomBytes(48).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const authorize = await fetch(`${BASE}/oauth/authorize`, {
    method: 'POST', redirect: 'manual', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: creds.client_id, redirect_uri: CALLBACK, state: TAG,
      code_challenge: challenge, code_challenge_method: 'S256', scope: 'maze', resource: `${BASE}/mcp`,
      username: USER, password: PASSWORD, action: 'allow' }),
  });
  if (authorize.status !== 303) throw new Error(`autorisation OAuth : ${authorize.status} ${await authorize.text()}`);
  const code = new URL(authorize.headers.get('location')).searchParams.get('code');
  const token = await fetch(`${BASE}/oauth/token`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code, redirect_uri: CALLBACK,
      code_verifier: verifier, client_id: creds.client_id, client_secret: creds.client_secret }),
  });
  if (!token.ok) throw new Error(`jeton OAuth : ${token.status} ${await token.text()}`);
  return (await token.json()).access_token;
}

let rpcId = 0;
async function requestUpload(access, args) {
  rpcId += 1;
  const res = await fetch(`${BASE}/mcp`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${access}`, 'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rpcId, method: 'tools/call',
      params: { name: 'request_upload', arguments: args } }),
  });
  const { result } = await res.json();
  if (result.isError) throw new Error(`request_upload : ${result.content[0].text}`);
  return JSON.parse(result.content[0].text);
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

  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Dépôts ${TAG}` })).id;
  const npc = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Ysolde de Valdis' });
  const place = await api(a, 'POST', `/api/campaigns/${cid}/places`, { name: 'Port-Sorn' });
  const doc = await api(a, 'POST', `/api/campaigns/${cid}/documents`, { title: 'Le bal masqué' });
  const access = await connectClaude(a, cid);
  check(Boolean(access), 'connecteur Claude autorisé (OAuth réel)');

  const heading = (page) => page.getByRole('heading', { name: 'Déposer une image' });
  const deposit = (page) => page.getByRole('button', { name: 'Déposer l’image' });

  // 1. Galerie d'un PNJ : choix du fichier, légende proposée par Claude, succès, suivi du ticket.
  const t1 = await requestUpload(access, { campaign_id: cid, target: 'npc', target_id: npc.id,
    caption: 'Portrait au bal' });
  check(t1.url.startsWith(`${BASE}/#/depot/`) && t1.valid_minutes === 10, 'request_upload : lien de la page de dépôt');
  await a.goto(t1.url);
  await heading(a).waitFor({ timeout: 15000 });
  check((await a.locator('.upload-page__target').textContent()).includes('Image pour la fiche du PNJ Ysolde de Valdis'),
    'rappel de la cible : fiche du PNJ');
  check(await a.getByLabel('Légende').inputValue() === 'Portrait au bal', 'légende proposée par Claude préremplie');
  check(await deposit(a).isDisabled(), '« Déposer l’image » inactif sans fichier');
  await a.screenshot({ path: path.join(SHOTS, 'depot-ordinateur.png') });
  await a.locator('.upload-page input[type=file]').setInputFiles({ name: 'ysolde.png', mimeType: 'image/png',
    buffer: png(480, 640) });
  await a.locator('.upload-page__preview img').waitFor({ timeout: 5000 });
  check((await a.locator('.upload-page__name').textContent()) === 'ysolde.png', 'aperçu : nom du fichier choisi');
  await a.screenshot({ path: path.join(SHOTS, 'depot-apercu.png') });
  await deposit(a).click();
  await a.getByRole('heading', { name: 'Image déposée' }).waitFor({ timeout: 15000 });
  check(true, 'dépôt réussi');
  await a.screenshot({ path: path.join(SHOTS, 'depot-succes.png') });
  const s1 = await requestUpload(access, { ticket: t1.ticket });
  check(s1.state === 'used' && Number.isInteger(s1.image_id), 'suivi du ticket : used, image_id');
  const detail = await api(a, 'GET', `/api/npcs/${npc.id}`);
  const img = detail.images.find((i) => i.id === s1.image_id);
  check(img && img.caption === 'Portrait au bal' && detail.portrait_image_id === s1.image_id,
    'image dans la galerie du PNJ, légendée, devenue son portrait');
  await a.getByRole('link', { name: 'Ouvrir la fiche' }).click();
  await a.waitForURL(new RegExp(`#/c/${cid}/personnages/npc-${npc.id}`), { timeout: 10000 });
  await a.locator('.upload-page').waitFor({ state: 'detached', timeout: 10000 });
  check(true, '« Ouvrir la fiche » mène à la fiche du PNJ');

  // 2. Lien déjà utilisé.
  await a.goto(t1.url);
  await a.getByRole('heading', { name: 'Lien déjà utilisé' }).waitFor({ timeout: 15000 });
  check(true, 'lien rouvert : « Lien déjà utilisé »');

  // 3. Bibliothèque : collage depuis le presse-papiers (Ctrl+V).
  const t2 = await requestUpload(access, { campaign_id: cid, target: 'library' });
  await a.goto(t2.url);
  await heading(a).waitFor({ timeout: 15000 });
  check((await a.locator('.upload-page__target').textContent()).includes('bibliothèque de la campagne'),
    'rappel de la cible : bibliothèque');
  await a.evaluate((bytes) => {
    const data = new DataTransfer();
    data.items.add(new File([new Uint8Array(bytes)], 'image.png', { type: 'image/png' }));
    window.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, [...png(300, 200, [60, 90, 150])]);
  await a.locator('.upload-page__preview img').waitFor({ timeout: 5000 });
  check(true, 'Ctrl+V : image collée en aperçu');
  await deposit(a).click();
  await a.getByRole('heading', { name: 'Image déposée' }).waitFor({ timeout: 15000 });
  const s2 = await requestUpload(access, { ticket: t2.ticket });
  const library = (await api(a, 'GET', `/api/admin/campaigns/${cid}/images`)).images;
  check(library.some((i) => i.source === 'library' && i.id === s2.library_image_id), 'image collée dans la bibliothèque');

  // 4. Fiche rédigée : glisser-déposer ; l'outil renvoie le HTML à insérer.
  const t3 = await requestUpload(access, { campaign_id: cid, target: 'document', target_id: doc.id });
  await a.goto(t3.url);
  await heading(a).waitFor({ timeout: 15000 });
  check(await a.getByLabel('Légende').count() === 0, 'fiche rédigée : pas de légende');
  await a.evaluate((bytes) => {
    const data = new DataTransfer();
    data.items.add(new File([new Uint8Array(bytes)], 'bal.png', { type: 'image/png' }));
    window.dispatchEvent(new DragEvent('dragover', { dataTransfer: data, bubbles: true, cancelable: true }));
    window.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
  }, [...png(400, 300, [110, 70, 140])]);
  await a.locator('.upload-page__preview img').waitFor({ timeout: 5000 });
  check(true, 'glisser-déposer : image en aperçu');
  await deposit(a).click();
  await a.getByRole('heading', { name: 'Image déposée' }).waitFor({ timeout: 15000 });
  const s3 = await requestUpload(access, { ticket: t3.ticket });
  check(s3.html.includes(`data-doc-image="${s3.document_image_id}"`), 'suivi : HTML de l’illustration à insérer');

  // 5. Mauvais format refusé par la page, ticket intact.
  const t4 = await requestUpload(access, { campaign_id: cid, target: 'place', target_id: place.id });
  await a.goto(t4.url);
  await heading(a).waitFor({ timeout: 15000 });
  await a.locator('.upload-page input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain',
    buffer: Buffer.from('pas une image') });
  await a.getByText('Formats acceptés : JPEG, PNG, WebP, GIF').waitFor({ timeout: 5000 });
  check(true, 'fichier texte refusé : formats acceptés rappelés');

  // 6. Téléphone : même ticket, dépôt par choix de fichier.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'fr-FR', isMobile: true,
    hasTouch: true, storageState: await ctx.storageState() });
  const p = await phone.newPage();
  p.on('pageerror', (e) => errors.push(`pageerror (téléphone): ${e.message}`));
  await p.goto(t4.url);
  await heading(p).waitFor({ timeout: 15000 });
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, 'téléphone : pas de défilement horizontal');
  await p.screenshot({ path: path.join(SHOTS, 'depot-telephone.png') });
  await p.locator('.upload-page input[type=file]').setInputFiles({ name: 'port-sorn.png', mimeType: 'image/png',
    buffer: png(640, 360, [60, 130, 140]) });
  await p.locator('.upload-page__preview img').waitFor({ timeout: 5000 });
  await p.screenshot({ path: path.join(SHOTS, 'depot-telephone-apercu.png') });
  await deposit(p).click();
  await p.getByRole('heading', { name: 'Image déposée' }).waitFor({ timeout: 15000 });
  await p.screenshot({ path: path.join(SHOTS, 'depot-telephone-succes.png') });
  check((await requestUpload(access, { ticket: t4.ticket })).state === 'used', 'téléphone : image déposée sur le lieu');
  await phone.close();

  // 7. Lien expiré : horloge du navigateur avancée de 11 minutes.
  const t5 = await requestUpload(access, { campaign_id: cid, target: 'library' });
  const late = await ctx.newPage();
  await late.clock.install();
  await late.goto(t5.url);
  await heading(late).waitFor({ timeout: 15000 });
  await late.clock.fastForward('11:00');
  await late.getByRole('heading', { name: 'Lien expiré' }).waitFor({ timeout: 10000 });
  check(true, 'au-delà de 10 minutes : « Lien expiré »');
  await late.screenshot({ path: path.join(SHOTS, 'depot-expire.png') });
  await late.close();

  // 8. Sans session : connexion, puis retour à la page de dépôt.
  const anon = await browser.newContext({ viewport: { width: 1200, height: 860 }, locale: 'fr-FR' });
  const q = await anon.newPage();
  await q.goto(t5.url);
  await q.locator('#login-username').waitFor({ timeout: 15000 });
  await q.fill('#login-username', USER);
  await q.fill('#login-password', PASSWORD);
  await q.locator('form button[type=submit]').click();
  await heading(q).waitFor({ timeout: 15000 });
  check(q.url().includes('#/depot/'), 'connexion demandée, puis retour à la page de dépôt');
  await anon.close();

  // 9. MJ sans accès à la campagne : lien inaccessible.
  const mjPassword = `mj-${randomBytes(6).toString('base64url')}`;
  await api(a, 'POST', '/api/admin/users', { username: `mj${TAG}`, role: 'mj', password: mjPassword, campaign_ids: [] });
  const other = await browser.newContext({ viewport: { width: 1200, height: 860 }, locale: 'fr-FR' });
  const m = await other.newPage();
  await m.goto(t5.url);
  await m.locator('#login-username').waitFor({ timeout: 15000 });
  await m.fill('#login-username', `mj${TAG}`);
  await m.fill('#login-password', mjPassword);
  await m.locator('form button[type=submit]').click();
  await m.getByRole('heading', { name: 'Lien de dépôt inaccessible' }).waitFor({ timeout: 15000 });
  check((await m.locator('.upload-page__state').textContent()).includes('Lien de dépôt introuvable'),
    'autre MJ sans la campagne : lien introuvable');
  await m.screenshot({ path: path.join(SHOTS, 'depot-inaccessible.png') });
  await other.close();

  if (errors.length) throw new Error(`Erreurs dans la page :\n${errors.join('\n')}`);
  ok = true;
} finally {
  await browser.close();
}
step(ok ? `Parcours terminé, captures dans ${SHOTS}` : 'Parcours en échec');
process.exit(ok ? 0 : 1);
