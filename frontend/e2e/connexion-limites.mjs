// Parcours navigateur de la vérification finale de l'audit (R4, R1) : ce qu'affiche l'écran de connexion quand
// les essais de mot de passe sont limités (backend/maze/throttle.py).
//   1. Flot d'essais faux simultanés sur le compte, depuis 30 adresses (X-Forwarded-For) : la file du compte et
//      ses deux essais uniques hors file sont pris ; l'écran de connexion, depuis une adresse neuve, affiche « Trop de
//      tentatives en cours sur ce compte. Réessayez dans N secondes. » (Retry-After : N, quelques secondes) ; après ce
//      délai, le bon mot de passe passe (essai unique hors file, R1) et l'interface s'ouvre.
//   2. Huit essais faux depuis une même adresse : le suivant affiche « Trop de tentatives depuis cette adresse.
//      Réessayez dans 10 minutes. » (Retry-After proche de 600).
//
// Base vide ou déjà initialisée ; l'instance doit croire les en-têtes X-Forwarded-For venus de 127.0.0.1 (défaut
// d'uvicorn sans FORWARDED_ALLOW_IPS, cas de run-all.sh). Usage : node connexion-limites.mjs [url]
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (dossier des captures). Playwright : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const candidates = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean),
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
const NET = 10 + (randomBytes(1)[0] % 200);   // adresses propres à ce passage (instance existante)
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };

function watch(page, label) {
  page.on('pageerror', (e) => errors.push(`${label} pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`${label} console: ${m.text()}`); });
}

async function loginRaw(username, password, forwarded) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST', body: JSON.stringify({ username, password }),
    headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': forwarded },
  });
  return res.status;
}

/** Envoie le formulaire de connexion ; renvoie la réponse de /api/auth/login. */
async function submit(page, username, password) {
  await page.fill('#login-username', username);
  await page.fill('#login-password', password);
  const response = page.waitForResponse((r) => r.url().endsWith('/api/auth/login'), { timeout: 30000 });
  await page.locator('form button[type=submit]').click();
  return response;
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  // Compte : premier lancement (création par l'API, sans connexion de cette adresse) ou compte existant.
  const status = await (await fetch(`${BASE}/api/auth/status`)).json();
  if (status.setup_required) {
    const res = await fetch(`${BASE}/api/auth/setup`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: USER, display_name: 'Admin E2E', password: PASSWORD }) });
    check(res.ok, `premier administrateur « ${USER} » créé`);
  }

  // 1. Flot simultané depuis 30 adresses : file du compte et essais uniques pris.
  const flood = Promise.all(Array.from({ length: 30 }, (_, i) => loginRaw(USER, 'mauvais-mot-de-passe', `10.${NET}.0.${i + 1}`)));
  await new Promise((r) => setTimeout(r, 500));
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, locale: 'fr-FR',
    extraHTTPHeaders: { 'X-Forwarded-For': `10.${NET}.1.1` } });
  const page = await ctx.newPage();
  watch(page, 'A');
  await page.goto(BASE + '/');
  await page.locator('#login-username').waitFor({ timeout: 15000 });
  let res = await submit(page, USER, PASSWORD);
  check(res.status() === 429, `connexion pendant le flot : 429 (reçu ${res.status()})`);
  const retry = Number(res.headers()['retry-after']);
  check(retry >= 1 && retry <= 10, `Retry-After de quelques secondes (${retry})`);
  const alert = page.locator('.form-error[role=alert]');
  const shown = await alert.textContent();
  check(shown === `Trop de tentatives en cours sur ce compte. Réessayez dans ${retry} seconde${retry > 1 ? 's' : ''}.`,
    `message affiché : « ${shown} »`);
  await page.screenshot({ path: path.join(SHOTS, 'connexion-creneau.png') });
  const codes = await flood;
  check(codes.filter((c) => c === 401).length <= 10 && codes.every((c) => c === 401 || c === 429),
    `flot : ${codes.filter((c) => c === 401).length} essais vérifiés sur 30, les autres refusés`);
  await new Promise((r) => setTimeout(r, retry * 1000 + 200));
  res = await submit(page, USER, PASSWORD);
  check(res.status() === 200, `après ${retry} s, le bon mot de passe passe (essai unique hors file)`);
  await page.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  check(true, 'interface ouverte');

  // 2. Limite de l'adresse : 8 essais faux, puis le formulaire.
  const ctx2 = await browser.newContext({ viewport: { width: 1200, height: 800 }, locale: 'fr-FR',
    extraHTTPHeaders: { 'X-Forwarded-For': `10.${NET}.2.1` } });
  const page2 = await ctx2.newPage();
  watch(page2, 'B');
  await Promise.all(Array.from({ length: 8 }, (_, i) => loginRaw(`inconnu-${i}`, 'faux', `10.${NET}.2.1`)));
  await page2.goto(BASE + '/');
  await page2.locator('#login-username').waitFor({ timeout: 15000 });
  res = await submit(page2, USER, PASSWORD);
  const wait = Number(res.headers()['retry-after']);
  check(res.status() === 429 && wait > 590 && wait <= 600, `adresse : 429, Retry-After ${wait} s`);
  const shown2 = await page2.locator('.form-error[role=alert]').textContent();
  check(shown2 === 'Trop de tentatives depuis cette adresse. Réessayez dans 10 minutes.', `message affiché : « ${shown2} »`);
  await page2.screenshot({ path: path.join(SHOTS, 'connexion-adresse.png') });
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `echec-connexion-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
const relevant = errors.filter((e) => !/favicon|401|429|Unauthorized|Too Many/.test(e));
if (relevant.length) { console.log('Erreurs navigateur :'); relevant.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `CONNEXION OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})`
  : 'CONNEXION ÉCHEC');
process.exit(ok && !relevant.some((e) => /pageerror/.test(e)) ? 0 : 1);
