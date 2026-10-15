// Parcours navigateur du lot 2 de l'audit (Playwright, Chromium headless) : Administration > Mise à jour.
// - Audit H2 : l'interface lit l'état du service maze-updater dans le dossier d'état (MAZE_STATUS_DIR, en lecture
//   seule en production), et à défaut dans l'ancien emplacement (dossier de contrôle) ; elle n'écrit que la demande.
// - Audit M20 : échec et retour automatique à la version précédente affichés clairement, code sur le serveur
//   différent de la version en service signalé.
// Le script joue le rôle du service de l'hôte en écrivant lui-même les fichiers d'état.
//
// Instance à démarrer avec MAZE_CONTROL_DIR, MAZE_STATUS_DIR (dossiers vides) et MAZE_VERSION=abc1234, par ex. :
//   export MAZE_CONTROL_DIR=$(mktemp -d) MAZE_STATUS_DIR=$(mktemp -d) MAZE_VERSION=abc1234
//   (cd backend && python3 -m uvicorn maze.app:app_factory --factory --host 127.0.0.1 --port 8765)
// ou plus simplement : frontend/e2e/run-all.sh mise-a-jour (instance et dossiers préparés par le script).
// Usage : MAZE_E2E_CONTROL_DIR=$MAZE_CONTROL_DIR MAZE_E2E_STATUS_DIR=$MAZE_STATUS_DIR node mise-a-jour.mjs [url]
// Variables : MAZE_E2E_USER (admin), MAZE_E2E_PASSWORD (requis si la base a déjà un compte), HEADED=1,
//             MAZE_E2E_SHOTS (dossier des captures). Playwright : module global, trouvé par NODE_PATH.
import { createRequire } from 'node:module';
import { randomBytes } from 'node:crypto';
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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
const CONTROL = process.env.MAZE_E2E_CONTROL_DIR;
const STATUS = process.env.MAZE_E2E_STATUS_DIR;
if (!CONTROL || !STATUS) throw new Error('MAZE_E2E_CONTROL_DIR et MAZE_E2E_STATUS_DIR requis (dossiers de l’instance)');
mkdirSync(SHOTS, { recursive: true });

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const errors = [];
const check = (cond, msg) => { if (!cond) throw new Error(msg); step(`OK : ${msg}`); };
const files = (dir) => readdirSync(dir).sort();
const clear = (dir) => { for (const f of readdirSync(dir)) rmSync(path.join(dir, f), { recursive: true, force: true }); };

function hostWrites(dir, entries) {
  for (const [name, content] of Object.entries(entries)) writeFileSync(path.join(dir, name), content);
}

async function openSection(page) {
  await page.goto(`${BASE}/#/admin/mise-a-jour`);
  await page.reload();      // même adresse : rechargement pour relire l'état
  await page.getByRole('heading', { name: 'Mise à jour', exact: true }).first().waitFor({ timeout: 15000 });
  await page.locator('.facts').waitFor({ timeout: 15000 });
}

const browser = await chromium.launch({ headless: !process.env.HEADED });
let ok = false;
try {
  clear(CONTROL);
  clear(STATUS);
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: 'fr-FR' });
  const a = await ctx.newPage();
  a.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  a.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  await a.goto(BASE + '/');
  // Premier lancement ou connexion : attendre que l'un des deux formulaires soit affiché.
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

  // 1. Service absent : boutons inactifs.
  await openSection(a);
  check(await a.locator('.notice', { hasText: 'pas installé' }).isVisible(), 'service absent : « pas installé »');
  check(await a.getByRole('button', { name: 'Mettre à jour' }).isDisabled(), 'bouton « Mettre à jour » inactif');

  // 2. Transition : état laissé dans le dossier de contrôle par l'ancien service, dossier d'état vide.
  hostWrites(CONTROL, {
    state: 'success\n',
    info: 'current=abc1234\navailable=abc1234\nmessage=Mise à jour terminée (version abc1234).\ncheck_message=Maze est à jour.\n',
  });
  await openSection(a);
  await a.locator('h2', { hasText: 'Dernière mise à jour réussie' }).waitFor({ timeout: 10000 });
  check(await a.getByRole('button', { name: 'Mettre à jour' }).isEnabled(), 'ancien emplacement lu (transition) : état « réussie », boutons actifs');
  check(await a.locator('[data-update-outcome]').count() === 0, 'aucune alerte après une réussite');

  // 3. Nouveau service : le dossier d'état prime. Retour automatique à la version précédente.
  hostWrites(STATUS, {
    state: 'rolledback\n',
    info: 'current=abc1234\navailable=def5678\nfinished_at=2026-10-14T10:20:00Z\n'
      + 'message=La nouvelle version ne répondait pas : retour automatique à la version précédente (abc1234), de nouveau en service. Voir le journal.\n',
    'commits.tsv': 'def5678\t2026-10-14T09:00:00Z\tNouvelle fonction\n',
    'update.log': '[10:19:00] Retour automatique à la version précédente (abc1234)\nVersion précédente (abc1234) de nouveau en service.\n',
  });
  await openSection(a);
  const rolled = a.locator('[data-update-outcome="rolledback"]');
  await rolled.waitFor({ timeout: 10000 });
  const rolledText = await rolled.textContent();
  check(rolledText.includes('retour à la version précédente') && rolledText.includes('Version en service : abc1234'),
    'retour automatique : alerte claire avec la version en service');
  check(await a.locator('.state-dot--rolledback').count() === 1, 'pastille d’état « annulée »');
  check(await a.locator('details.log[open] pre', { hasText: 'Retour automatique' }).isVisible(), 'journal ouvert');
  check(await a.locator('[data-version-mismatch]').count() === 0, 'code et version en service identiques : rien à signaler');
  await a.screenshot({ path: path.join(SHOTS, 'lot2-retour-arriere.png') });

  // 4. Échec avant le redémarrage avec un code resté sur la nouvelle version (ancien script) : signalé.
  hostWrites(STATUS, {
    state: 'failed\n',
    info: 'current=def5678\navailable=def5678\nmessage=La mise à jour a échoué avant le redémarrage : la version précédente est toujours en service. Voir le journal.\n',
    'commits.tsv': '',
  });
  await openSection(a);
  const failed = a.locator('[data-update-outcome="failed"]');
  await failed.waitFor({ timeout: 10000 });
  check((await failed.textContent()).includes('toujours en service'), 'échec : alerte claire');
  const mismatch = a.locator('[data-version-mismatch]');
  check((await mismatch.textContent()).includes('def5678') && (await mismatch.textContent()).includes('abc1234'),
    'code sur le serveur différent de la version en service : signalé');

  // 5. Demande : seule control/request est écrite par l'interface ; rien dans le dossier d'état.
  const statusBefore = files(STATUS);
  await a.getByRole('button', { name: 'Mettre à jour' }).click();
  await a.locator('.modal[role=dialog]').getByRole('button', { name: 'Lancer la mise à jour' }).click();
  await a.locator('h2', { hasText: 'Demande transmise au serveur' }).waitFor({ timeout: 10000 });
  check(readFileSync(path.join(CONTROL, 'request'), 'utf8').split('\n')[0] === 'update', 'demande « update » déposée dans le dossier de contrôle');
  check(JSON.stringify(files(STATUS)) === JSON.stringify(statusBefore), 'dossier d’état intact');
  // Le service la traite (ici, simulé) : demande retirée, réussite publiée dans le dossier d'état.
  rmSync(path.join(CONTROL, 'request'));
  clear(CONTROL);
  hostWrites(STATUS, {
    state: 'success\n',
    info: 'current=abc1234\navailable=abc1234\nmessage=Mise à jour terminée (version abc1234).\ncheck_message=Maze est à jour.\n',
  });
  await a.locator('h2', { hasText: 'Dernière mise à jour réussie' }).waitFor({ timeout: 15000 });
  check(await a.locator('[data-update-outcome], [data-version-mismatch]').count() === 0, 'réussite : plus d’alerte');
  ok = true;
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const p of browser.contexts().flatMap((c) => c.pages())) {
    await p.screenshot({ path: path.join(SHOTS, `echec-lot2-${Date.now()}.png`) }).catch(() => {});
  }
} finally {
  await browser.close();
}
const relevant = errors.filter((e) => !/favicon|401|Unauthorized/.test(e));
if (relevant.length) { console.log('Erreurs navigateur :'); relevant.forEach((e) => console.log('  ' + e)); }
console.log(ok ? `LOT 2 OK (compte ${USER}${process.env.MAZE_E2E_PASSWORD ? '' : `, mot de passe généré : ${PASSWORD}`})` : 'LOT 2 ÉCHEC');
process.exit(ok && !relevant.length ? 0 : 1);
