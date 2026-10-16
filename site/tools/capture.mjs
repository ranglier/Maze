#!/usr/bin/env node
// Screenshots of the presentation site (site/img/*.webp), taken from a throwaway Maze instance.
//
// What it does, in order:
//   1. creates an empty data folder and loads the English demo campaign (python -m maze.demo --lang en);
//   2. starts Maze on 127.0.0.1 the way frontend/e2e/run-all.sh does (uvicorn maze.app:app_factory);
//   3. creates the first administrator from the setup screen, then adds through the API what the demo lacks:
//      a campaign page, scenarios with read-aloud and GM secret boxes, a comment thread, an interactive map
//      (background drawn by this script) with pins, a co-GM account and a backup;
//   4. takes the screenshots in an en-US browser (1440 x 900, device scale 1.5) and converts them to WebP;
//   5. stops the server and deletes the data folder (MAZE_CAPTURE_KEEP=1 keeps it).
//
// Usage, from the repository root (interface built first: cd frontend && npm run build):
//   NODE_PATH="$(npm root -g)" node site/tools/capture.mjs
// Variables:
//   MAZE_STATIC_DIR      built interface (default: frontend/dist)
//   PYTHON               interpreter with the server dependencies, Pillow included (default: python3)
//   MAZE_CAPTURE_PORT    port of the throwaway instance (default: 8797)
//   MAZE_CAPTURE_KEEP=1  keep the data folder, the server log and the raw PNG files
//   NODE_PATH            folder containing the playwright module (Playwright is not a project dependency)
import { spawn, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, openSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const candidates = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean),
  path.join(process.env.HOME || '/', '.npm-global/lib/node_modules'), '/opt/npm-tools/node_modules'];
let chromium;
for (const dir of candidates) {
  try { ({ chromium } = require(path.join(dir, 'playwright'))); break; } catch { /* next */ }
}
if (!chromium) throw new Error('playwright module not found: set NODE_PATH (for example NODE_PATH="$(npm root -g)")');

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO = path.resolve(SITE, '..');
const OUT = path.join(SITE, 'img');
const PYTHON = process.env.PYTHON || 'python3';
const PORT = Number(process.env.MAZE_CAPTURE_PORT || 8797);
const BASE = `http://127.0.0.1:${PORT}`;
const STATIC = path.resolve(process.env.MAZE_STATIC_DIR || path.join(REPO, 'frontend/dist'));
const KEEP = process.env.MAZE_CAPTURE_KEEP === '1';
const SCALE = 1.5;
const VIEWPORT = { width: 1440, height: 900 };

if (!existsSync(path.join(STATIC, 'index.html'))) {
  console.error(`No built interface in ${STATIC}: run "cd frontend && npm run build" first, or set MAZE_STATIC_DIR.`);
  process.exit(1);
}

const step = (msg) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${msg}`);
const WORK = mkdtempSync(path.join(tmpdir(), 'maze-capture-'));
const RAW = path.join(WORK, 'raw');
mkdirSync(RAW, { recursive: true });
mkdirSync(OUT, { recursive: true });
const version = spawnSync('git', ['-C', REPO, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).stdout.trim() || 'dev';
const ENV = {
  ...process.env,
  MAZE_DATA_DIR: path.join(WORK, 'data'),
  MAZE_STATIC_DIR: STATIC,
  MAZE_CONTROL_DIR: path.join(WORK, 'control'),
  MAZE_STATUS_DIR: path.join(WORK, 'status'),
  MAZE_VERSION: version,
  MAZE_DEFAULT_LOCALE: 'en',
  PYTHONDONTWRITEBYTECODE: '1',
};
for (const dir of ['data', 'control', 'status']) mkdirSync(path.join(WORK, dir), { recursive: true });

function python(args) {
  const res = spawnSync(PYTHON, args, { cwd: path.join(REPO, 'backend'), env: ENV, encoding: 'utf8' });
  if (res.status !== 0) throw new Error(`${PYTHON} ${args.join(' ')} failed:\n${res.stdout}${res.stderr}`);
  return res.stdout;
}

/** WebP from a PNG screenshot (Pillow, already a server dependency). */
function toWebp(png, name) {
  const target = path.join(OUT, `${name}.webp`);
  python(['-c', 'import sys; from PIL import Image; Image.open(sys.argv[1]).convert("RGB").save(sys.argv[2], "WEBP", quality=86, method=6)',
    png, target]);
  return target;
}

// ---------------------------------------------------------------------------------------------------------------
// Map background: a harbour town on a drowned bay, drawn as SVG and rasterised by the browser (no image asset).
function mapSvg(w = 2400, h = 1600) {
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  const pts = (list) => list.map(([x, y]) => `${x},${y}`).join(' ');
  const land = [[930, 0], [990, 160], [940, 330], [990, 470], [1080, 590], [1160, 640], [1120, 760], [1180, 880],
    [1060, 950], [880, 1030], [760, 1150], [700, 1260], [760, 1350], [900, 1400], [1080, 1370], [1280, 1430],
    [1420, 1600], [w, 1600], [w, 0]];
  const isle = [[330, 250], [420, 210], [520, 240], [540, 320], [470, 380], [360, 360]];
  const out = [];
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`);
  out.push(`<defs>
    <radialGradient id="sea" cx="35%" cy="60%" r="80%"><stop offset="0" stop-color="#3b6170"/><stop offset="1" stop-color="#1f3a47"/></radialGradient>
    <pattern id="waves" width="90" height="40" patternUnits="userSpaceOnUse">
      <path d="M5 25 q10 -8 20 0 t20 0" fill="none" stroke="#5c8592" stroke-width="2" opacity=".35"/></pattern>
    <filter id="rough"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="3"/>
      <feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .10 0"/><feComposite in2="SourceGraphic" operator="in"/></filter>
  </defs>`);
  out.push(`<rect width="${w}" height="${h}" fill="url(#sea)"/><rect width="${w}" height="${h}" fill="url(#waves)"/>`);
  // Drowned old town, visible under the water of the bay.
  for (let i = 0; i < 70; i += 1) {
    const x = 260 + rnd() * 460; const y = 760 + rnd() * 420; const s = 18 + rnd() * 34;
    out.push(`<rect x="${x.toFixed(0)}" y="${y.toFixed(0)}" width="${s.toFixed(0)}" height="${(s * (0.6 + rnd() * 0.6)).toFixed(0)}" fill="none" stroke="#7fa3ad" stroke-width="2" opacity=".28" transform="rotate(${(rnd() * 8 - 4).toFixed(1)} ${x.toFixed(0)} ${y.toFixed(0)})"/>`);
  }
  out.push('<path d="M300 980 L720 960 M480 760 L520 1180" stroke="#7fa3ad" stroke-width="4" opacity=".22" fill="none"/>');
  // Shallows and coast.
  for (const poly of [land, isle]) {
    out.push(`<polygon points="${pts(poly)}" fill="#4f7a82" stroke="#4f7a82" stroke-width="70" stroke-linejoin="round" opacity=".55"/>`);
    out.push(`<polygon points="${pts(poly)}" fill="none" stroke="#a9c4c4" stroke-width="10" stroke-linejoin="round" opacity=".45"/>`);
    out.push(`<polygon points="${pts(poly)}" fill="#a69c7a" stroke="#5d5640" stroke-width="4" stroke-linejoin="round"/>`);
  }
  out.push(`<rect width="${w}" height="${h}" fill="#000" filter="url(#rough)"/>`);
  // Fields to the east.
  for (let i = 0; i < 26; i += 1) {
    const x = 1750 + rnd() * 560; const y = 820 + rnd() * 660; const fw = 70 + rnd() * 110; const fh = 50 + rnd() * 80;
    out.push(`<rect x="${x.toFixed(0)}" y="${y.toFixed(0)}" width="${fw.toFixed(0)}" height="${fh.toFixed(0)}" fill="${rnd() > 0.5 ? '#b4a97f' : '#959067'}" stroke="#6f6850" stroke-width="2" opacity=".8" transform="rotate(${(rnd() * 20 - 10).toFixed(1)} ${x.toFixed(0)} ${y.toFixed(0)})"/>`);
  }
  // Woods to the north-east.
  for (let i = 0; i < 160; i += 1) {
    const x = 1700 + rnd() * 650; const y = 40 + rnd() * 420; const r = 14 + rnd() * 12;
    out.push(`<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${r.toFixed(0)}" fill="#6e7a56" stroke="#4f5940" stroke-width="2"/>`);
  }
  // Cliff between the lower and upper town.
  let cliff = 'M1150 600';
  for (let x = 1150; x <= 1900; x += 25) cliff += ` L${x} ${(600 - (x - 1150) * 0.12 + Math.sin(x / 40) * 10).toFixed(0)}`;
  out.push(`<path d="${cliff}" fill="none" stroke="#5d5640" stroke-width="5"/>`);
  for (let x = 1160; x <= 1890; x += 16) {
    const y = 600 - (x - 1150) * 0.12 + Math.sin(x / 40) * 10;
    out.push(`<path d="M${x} ${y.toFixed(0)} l-6 22" stroke="#5d5640" stroke-width="2.5"/>`);
  }
  // Roads.
  out.push('<path d="M1180 780 C1400 820 1600 860 2400 900 M1500 470 C1700 420 1900 300 2400 260 M1380 820 C1450 700 1480 600 1500 470 M1300 1000 C1100 1150 950 1250 800 1300" fill="none" stroke="#6f6850" stroke-width="7" stroke-linecap="round" opacity=".85"/>');
  // Houses: harbour, lower town, upper town.
  const houses = (cx, cy, rx, ry, n, size, fill) => {
    for (let i = 0; i < n; i += 1) {
      const a = rnd() * Math.PI * 2; const d = Math.sqrt(rnd());
      const x = cx + Math.cos(a) * rx * d; const y = cy + Math.sin(a) * ry * d; const s = size * (0.7 + rnd() * 0.6);
      out.push(`<rect x="${x.toFixed(0)}" y="${y.toFixed(0)}" width="${s.toFixed(0)}" height="${(s * 0.7).toFixed(0)}" fill="${fill}" stroke="#3a3528" stroke-width="2" transform="rotate(${(rnd() * 30 - 15).toFixed(1)} ${x.toFixed(0)} ${y.toFixed(0)})"/>`);
    }
  };
  houses(1260, 780, 90, 80, 45, 22, '#7b6a52');
  houses(1400, 860, 170, 110, 120, 20, '#8a7658');
  houses(1560, 440, 200, 100, 60, 30, '#94816a');
  // Piers.
  for (const [x, y, len] of [[1160, 700, 120], [1150, 760, 150], [1170, 830, 110]]) {
    out.push(`<rect x="${x - len}" y="${y}" width="${len}" height="12" fill="#6b5a44" stroke="#3a3528" stroke-width="2"/>`);
  }
  // Watchtower and mansion.
  out.push('<circle cx="1105" cy="905" r="20" fill="#6b5a44" stroke="#3a3528" stroke-width="3"/>');
  out.push('<rect x="1620" y="390" width="70" height="46" fill="#a08c6b" stroke="#3a3528" stroke-width="3"/>');
  // Cave on the headland.
  out.push('<path d="M735 1300 q25 -40 50 0 z" fill="#2d2a22" stroke="#3a3528" stroke-width="3"/>');
  // Compass.
  out.push(`<g transform="translate(2200 1380)" stroke="#5d5640" stroke-width="3">
    <circle r="80" fill="none" opacity=".6"/><path d="M0 -110 L18 0 L0 110 L-18 0 Z" fill="#c9a45c"/>
    <path d="M-110 0 L0 14 L110 0 L0 -14 Z" fill="#a69c7a"/></g>`);
  out.push('</svg>');
  return out.join('\n');
}

// Positions of the pins on that map, as fractions of its width and height.
const PINS = {
  'The Harbour': [0.488, 0.47],
  'Lower town': [0.585, 0.535],
  'Upper town': [0.65, 0.28],
  'Sunken bay': [0.2, 0.6],
  'Cave of Sighs': [0.316, 0.8],
};

// ---------------------------------------------------------------------------------------------------------------
const server = { proc: null };
function stopServer() {
  if (server.proc && server.proc.exitCode === null) server.proc.kill();
  server.proc = null;
}
process.on('exit', stopServer);

async function waitHealth() {
  for (let i = 0; i < 60; i += 1) {
    try { if ((await fetch(`${BASE}/api/health`)).ok) return; } catch { /* not up yet */ }
    if (server.proc.exitCode !== null) break;
    await new Promise((r) => { setTimeout(r, 500); });
  }
  throw new Error(`Maze did not answer on ${BASE}; log: ${path.join(WORK, 'server.log')}`);
}

async function api(page, method, url, data, extra = {}) {
  const res = await page.request.fetch(BASE + url,
    { method, ...(data == null ? {} : { data }), headers: { 'Accept-Language': 'en-US' }, ...extra });
  if (!res.ok()) throw new Error(`${method} ${url}: ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

const mention = (kind, id, name) => `<a class="mention" data-mention="${kind}:${id}">${name}</a>`;

let ok = false;
let browser;
try {
  step(`Data folder: ${WORK}`);
  python(['-m', 'maze.demo', '--lang', 'en']);
  step('English demo campaign created');
  const log = openSync(path.join(WORK, 'server.log'), 'w');
  server.proc = spawn(PYTHON, ['-m', 'uvicorn', 'maze.app:app_factory', '--factory', '--host', '127.0.0.1', '--port', String(PORT)],
    { cwd: path.join(REPO, 'backend'), env: ENV, stdio: ['ignore', log, log] });
  await waitHealth();
  step(`Maze ${version} on ${BASE}`);

  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: SCALE, locale: 'en-US',
    timezoneId: 'Europe/Paris', colorScheme: 'dark' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  // First administrator, from the setup screen.
  const password = randomBytes(18).toString('base64url');
  await page.goto(`${BASE}/`);
  await page.locator('#setup-username').waitFor({ timeout: 15000 });
  await page.fill('#setup-username', 'morgane');
  await page.fill('#setup-name', 'Morgane');
  await page.fill('#setup-password', password);
  await page.fill('#setup-confirm', password);
  await page.locator('form button[type=submit]').click();
  await page.locator('#setup-username').waitFor({ state: 'detached', timeout: 15000 });
  step('Administrator created');

  // Demo data, looked up by name.
  const cid = (await api(page, 'GET', '/api/campaigns'))[0].id;
  const graph = await api(page, 'GET', `/api/campaigns/${cid}/graph`);
  const places = (await api(page, 'GET', `/api/campaigns/${cid}/places`)).places;
  const find = (list, name, what) => {
    const found = list.find((x) => x.name === name);
    if (!found) throw new Error(`${what} "${name}" not found in the English demo (maze/i18n/en/demo.json changed?)`);
    return found.id;
  };
  const npc = (name) => mention('npc', find(graph.npcs, name, 'NPC'), name);
  const faction = (name) => mention('faction', find(graph.factions, name, 'Faction'), name);
  const place = (name) => mention('place', find(places, name, 'Place'), name);

  // Campaign page and scenarios.
  const docs = await api(page, 'GET', `/api/campaigns/${cid}/documents`);
  const root = docs.root_id;
  const rootDoc = await api(page, 'GET', `/api/documents/${root}`);
  await api(page, 'PATCH', `/api/documents/${root}`, { base_revision: rootDoc.revision, content: [
    `<p>Ys is a merchant port slowly eaten by fog and smuggling. The players arrive a week before the royal inquiry into the Harbourmaster's ledgers, when everyone in town has something to hide.</p>`,
    '<h2>Threads</h2><ul>',
    `<li><p>Who falsified the ledgers, and who drowned ${npc('Elias Morn')} for finding out?</p></li>`,
    `<li><p>The debts of ${faction('House Valcourt')} to ${faction('The Grey Hand')}.</p></li>`,
    `<li><p>The great tide, five weeks from now, and what the ${faction('Cult of the Black Tide')} means to wake.</p></li>`,
    '</ul>',
  ].join('') });
  const newDoc = async (title, parent, status, content = '') => {
    const d = await api(page, 'POST', `/api/campaigns/${cid}/documents`, { title, parent_id: parent, content });
    if (status) await api(page, 'PATCH', `/api/documents/${d.id}`, { status });
    return d.id;
  };
  await newDoc('Prologue: arrival in Ys', root, 'joue');
  const boat = await newDoc('The empty boat', root, 'pret', [
    `<p>Three nights ago, ${npc('Brann Nutshell')} rowed out at dusk. His boat came back on the morning tide, empty, its nets cut. His sister hires the players to find out what happened.</p>`,
    "<h2>Opening: the fishermen's quay</h2>",
    '<div data-callout="read"><p>The fog has not lifted since dawn. Gulls circle a small boat dragged up on the shingle. Someone has painted a black wave on its bow, and the paint is still wet.</p></div>',
    `<p>The fishermen will not talk while ${npc('Tess Quicksilver')}, sent by ${npc('Captain Hart')}, walks the quay with her patrol. Away from the Watch, they whisper that Brann owed money to the ${faction('Cult of the Black Tide')}.</p>`,
    `<div data-callout="secret"><p>Brann is alive. He has been hiding in the ${place('Cave of Sighs')} since he saw ${npc('Ysolde de Valcourt')} hand a heavy purse to ${npc('Mother Oriane')}.</p></div>`,
    '<h2>Leads</h2>',
    '<table><tr><th><p>Clue</p></th><th><p>Where</p></th><th><p>Leads to</p></th></tr>',
    "<tr><td><p>Black wave on the bow</p></td><td><p>Fishermen's quay</p></td><td><p>The cult</p></td></tr>",
    `<tr><td><p>Brann's debts</p></td><td><p>${place('The Smoked Herring Tavern')}</p></td><td><p>${npc('Maëlis Corvin')}</p></td></tr>`,
    `<tr><td><p>A lady's glove caught in the nets</p></td><td><p>The boat</p></td><td><p>${faction('House Valcourt')}</p></td></tr>`,
    '</table>',
    '<h2>If the players stall</h2>',
    `<p>${npc('Silas the Mute')} follows them out of the tavern and leaves a fishing hook on their table: Maëlis wants a word.</p>`,
  ].join(''));
  await newDoc('Scene: at the quay', boat, 'pret');
  await newDoc('Handout: the ledger', boat, 'pret');
  await newDoc('The great tide', root, 'brouillon');
  step('Campaign page and scenarios written');

  // Co-GM account, assigned to the campaign: comment thread and live cursor in the editor.
  const coPassword = randomBytes(18).toString('base64url');
  await api(page, 'POST', '/api/admin/users', { username: 'alex', display_name: 'Alex', role: 'mj', password: coPassword,
    campaign_ids: [cid] });
  const coCtx = await browser.newContext({ viewport: VIEWPORT, locale: 'en-US', timezoneId: 'Europe/Paris' });
  const co = await coCtx.newPage();
  await co.goto(`${BASE}/`);
  await co.locator('#login-username').waitFor({ timeout: 15000 });
  await co.fill('#login-username', 'alex');
  await co.fill('#login-password', coPassword);
  await co.locator('form button[type=submit]').click();
  await co.locator('#login-username').waitFor({ state: 'detached', timeout: 15000 });
  const quote = 'the paint is still wet';
  const thread = await api(co, 'POST', `/api/documents/${boat}/comments`, { quote,
    body: "Still wet means whoever painted it is close by. Could Silas be watching from the nets?" });
  await api(page, 'POST', `/api/documents/${boat}/comments`, { parent_id: thread.id,
    body: 'Yes, and he runs if they spot him. Good hook for the tavern scene.' });
  const boatDoc = await api(page, 'GET', `/api/documents/${boat}`);
  await api(page, 'PATCH', `/api/documents/${boat}`, { base_revision: boatDoc.revision,
    content: boatDoc.content.replace(quote, `<span data-comment="${thread.id}">${quote}</span>`) });
  step('Comment thread added');

  // Interactive map: background drawn above, pins on the districts, the bay and the cave.
  const drawer = await browser.newPage({ viewport: { width: 2400, height: 1600 } });
  await drawer.setContent(`<body style="margin:0">${mapSvg()}</body>`);
  const mapPng = await drawer.locator('svg').screenshot({ type: 'png' });
  await drawer.close();
  const map = await api(page, 'POST', `/api/campaigns/${cid}/maps`, null, {
    multipart: { name: 'Ys and the sunken bay', file: { name: 'ys.png', mimeType: 'image/png', buffer: mapPng } } });
  for (const [name, [x, y]] of Object.entries(PINS)) {
    await api(page, 'PUT', `/api/maps/${map.id}/pins/${find(places, name, 'Place')}`, { x, y });
  }
  step('Interactive map created');

  // Backup (same command as the daily timer), so that the administration lists one.
  python(['-m', 'maze.backup']);
  step('Backup taken');

  // -------------------------------------------------------------------------------------------------------------
  // Sample of French interface texts (as in frontend/e2e/interface-anglaise.mjs): a screenshot that shows one fails.
  const FRENCH = /(?<![\p{L}])(Scénarios?|Personnages|Lieux|Enregistrer|Annuler|Modifier|Supprimer|Rechercher|Aucune?|Accroche|Fiches?|Nouveau|Nouvelle|Carte interactive|Brouillon|Prêt|Joué|Sommaire|Historique|Commentaires|Ajouter|Fermer|Vivant|Résumé|Sauvegardes?|quotidienne|manuelle|Administrateur|Chargement)(?![\p{L}])/u;
  const shots = [];
  const shoot = async (name, target = page) => {
    await target.evaluate(() => document.fonts.ready);
    const texts = await target.evaluate(() => [document.body.innerText, ...[...document.querySelectorAll('[title], [aria-label]')]
      .flatMap((el) => [el.getAttribute('title'), el.getAttribute('aria-label')]).filter(Boolean)]);
    const french = texts.flatMap((t) => t.split('\n')).filter((t) => FRENCH.test(t));
    if (french.length) throw new Error(`French text on the ${name} screen: ${french.slice(0, 5).join(' | ')}`);
    const file = path.join(RAW, `${name}.png`);
    await target.screenshot({ path: file });
    shots.push(toWebp(file, name));
    step(`Screenshot ${name}`);
  };
  const go = async (hash) => {
    await page.goto(`${BASE}/${hash}`);
    await page.waitForLoadState('networkidle');
  };
  await page.reload();

  // Campaign page, with the scenario tree in the sidebar.
  await go(`#/c/${cid}`);
  await page.locator('.ProseMirror').first().waitFor();
  await page.getByText('Threads').first().waitFor();
  await shoot('campaign');

  // Scenario editor: read-aloud and secret boxes, Alex's cursor, comment thread.
  await go(`#/c/${cid}/scenarios/${boat}`);
  await page.locator('.ProseMirror .callout[data-callout="read"]').waitFor();
  await co.goto(`${BASE}/#/c/${cid}/scenarios/${boat}`);
  await co.locator('.ProseMirror').first().waitFor();
  const target = co.locator('.ProseMirror p', { hasText: 'fishermen will not talk' }).first();
  await target.waitFor();
  await target.click();
  await co.evaluate((phrase) => {   // Alex selects a phrase: the selection shows in Morgane's editor
    const walker = document.createTreeWalker(document.querySelector('.ProseMirror'), NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const i = n.data.indexOf(phrase);
      if (i < 0) continue;
      const range = document.createRange();
      range.setStart(n, i); range.setEnd(n, i + phrase.length);
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      return;
    }
  }, 'owed money');
  await page.locator('.ProseMirror .remote-caret').first().waitFor({ timeout: 15000 });
  await page.locator('.doc-side__tabs [role=tab]').nth(1).click();
  await page.locator('.doc-side', { hasText: 'Silas' }).waitFor();
  await page.waitForTimeout(500);
  await shoot('editor');
  await coCtx.close();

  // Relationship graph of the scenario's cast.
  await go(`#/c/${cid}/carte?scenario=${boat}`);
  await page.locator('canvas').first().waitFor();
  await page.waitForTimeout(2500);   // layout settles
  await shoot('graph');

  // NPC and faction sheets.
  await go(`#/c/${cid}/personnages/npc-${find(graph.npcs, 'Maëlis Corvin', 'NPC')}`);
  await page.getByRole('heading', { name: 'Maëlis Corvin' }).first().waitFor();
  await shoot('npc');
  await go(`#/c/${cid}/personnages/faction-${find(graph.factions, 'House Valcourt', 'Faction')}`);
  await page.getByRole('heading', { name: 'House Valcourt' }).first().waitFor();
  await shoot('faction');

  // Interactive map with place names, the cave open: its sub-place in a ring, its sheet on the side.
  await page.evaluate((key) => localStorage.setItem(key, '1'), `maze:map-labels:${map.id}`);   // place names shown
  await go(`#/c/${cid}/cartes/${map.id}`);
  await page.reload();
  await page.waitForFunction(() => document.querySelector('.map-view__image')?.complete
    && document.querySelector('.map-view__image').naturalWidth > 0, null, { timeout: 15000 });
  await page.locator('.map-pin', { has: page.locator('.map-pin__label', { hasText: 'Cave of Sighs' }) }).click();
  await page.waitForTimeout(800);
  await shoot('map');

  // Administration > Backups.
  await go('#/admin/sauvegardes');
  await page.getByRole('heading', { name: 'Backups' }).first().waitFor();
  await page.waitForTimeout(500);
  await shoot('backups');

  if (errors.length) throw new Error(`Errors in the page:\n${errors.join('\n')}`);
  step(`Written: ${shots.map((s) => path.relative(REPO, s)).join(', ')}`);
  ok = true;
} finally {
  if (browser) await browser.close();
  stopServer();
  if (KEEP || !ok) console.log(`Data, server log and raw screenshots kept in ${WORK}`);
  else rmSync(WORK, { recursive: true, force: true });
}
process.exit(ok ? 0 : 1);
