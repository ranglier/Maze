// Parcours navigateur de non-régression des cartes interactives (Lieux > Carte interactive, MapsView.jsx), écrit
// avant le découpage du composant (audit QUAL-10) : import d'un fond, épingle d'un lieu (clic puis clic, et
// glisser-déposer depuis la liste), déplacement d'une épingle, zoom (boutons, molette) et déplacement de la carte,
// sous-lieux en anneaux (frères repliés en « +N autres », pages de 10, Échap), fiche du lieu à côté, noms et aspect
// des épingles, seconde carte qui représente un lieu (« Entrer », fil d'Ariane), changement de carte, retrait d'une
// épingle et suppression d'une carte.
//
// Base vide ou déjà initialisée. Usage : node cartes-interactives.mjs [url]   (défaut : $MAZE_URL ou http://127.0.0.1:8765)
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

  // Lieux : Ys > (Basse ville > (Taverne > Cave, Ruelles), Port, Haute ville) ; Baie > Archipel > 12 îlots.
  const cid = (await api(a, 'POST', '/api/campaigns', { name: `Cartes ${TAG}` })).id;
  const mk = async (name, parent = null, kind = 'lieu') => (await api(a, 'POST', `/api/campaigns/${cid}/places`,
    { name, kind, ...(parent ? { parent_id: parent } : {}) })).id;
  const ys = await mk('Ys', null, 'ville');
  const basse = await mk('Basse ville', ys, 'quartier');
  const taverne = await mk('Taverne du Hareng', basse, 'batiment');
  await mk('Cave', taverne, 'salle');
  await mk('Ruelles', basse);
  await mk('Port', ys, 'quartier');
  await mk('Haute ville', ys, 'quartier');
  const baie = await mk('Baie', null, 'nature');
  const archipel = await mk('Archipel', baie, 'nature');
  for (let i = 1; i <= 12; i += 1) await mk(`Îlot ${String(i).padStart(2, '0')}`, archipel, 'nature');
  const hart = await api(a, 'POST', `/api/campaigns/${cid}/npcs`, { name: 'Hart' });
  await api(a, 'POST', `/api/places/${ys}/links`, { entity_kind: 'npc', entity_id: hart.id, role: 'Gouverneur' });

  const mapView = a.locator('.map-view');
  // Boutons de la barre : leur libellé se masque quand la barre est étroite (nom accessible = title).
  const editButton = a.locator('.toolbar button[aria-pressed]');
  const image = a.locator('.map-view__image');
  const pin = (name) => a.locator('.map-pin', { has: a.locator('.map-pin__label', { hasText: name }) });
  const chip = (name) => a.locator('.map-chip', { has: a.locator('.map-chip__name', { hasText: name }) });
  const transform = async () => {
    const t = await image.getAttribute('style');
    const m = t.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/);
    return { x: Number(m[1]), y: Number(m[2]), k: Number(m[3]) };
  };
  /** Point de l'écran correspondant à (fx, fy) en fractions de l'image affichée. */
  const at = async (fx, fy) => {
    const box = await image.boundingBox();
    return { x: box.x + box.width * fx, y: box.y + box.height * fy };
  };
  const pinsOf = async (mid) => Object.fromEntries((await api(a, 'GET', `/api/maps/${mid}`)).pins.map((p) => [p.place_id, p]));

  // 1. Import d'un fond : première carte.
  await a.goto(`${BASE}/#/c/${cid}/cartes`);
  await a.reload();   // campagnes relues (créées par l'API après le chargement de la page)
  await a.getByRole('heading', { name: 'Aucune carte pour l’instant' }).waitFor({ timeout: 15000 });
  await a.getByRole('button', { name: 'Créer une carte' }).click();
  const dialog = a.locator('.modal', { hasText: 'Nouvelle carte' });
  await dialog.locator('#map-name').fill('Secteur');
  await dialog.locator('input[type=file]').setInputFiles({ name: 'secteur.png', mimeType: 'image/png', buffer: png(1600, 1000) });
  await image.waitFor({ timeout: 30000 });
  await a.waitForFunction(() => document.querySelector('.map-view__image')?.complete
    && document.querySelector('.map-view__image').naturalWidth > 0, null, { timeout: 15000 });
  await a.locator('.map-view__overlay').waitFor({ timeout: 10000 });
  const maps = (await api(a, 'GET', `/api/campaigns/${cid}/maps`)).maps;
  check(maps.length === 1 && maps[0].name === 'Secteur' && maps[0].width === 1600, 'fond importé : carte « Secteur » de 1600 px');
  const map1 = maps[0].id;
  check(a.url().endsWith(`/cartes/${map1}`), 'adresse de la nouvelle carte');
  check(await a.locator('.pin-list').isVisible(), 'mode « Placer les lieux » ouvert après la création');

  // 2. Épingles : clic sur un lieu puis sur la carte ; glisser-déposer depuis la liste.
  await a.locator('.pin-list__item', { hasText: 'Ys' }).first().click();
  await a.locator('.map-hint', { hasText: '« Ys »' }).waitFor();
  let p = await at(0.3, 0.4);
  await a.mouse.click(p.x, p.y);
  await pin('Ys').waitFor();
  await sleep(300);
  let pins = await pinsOf(map1);
  check(pins[ys] && Math.abs(pins[ys].x - 0.3) < 0.02 && Math.abs(pins[ys].y - 0.4) < 0.02, `Ys épinglé en (${pins[ys]?.x.toFixed(3)}, ${pins[ys]?.y.toFixed(3)})`);
  const box = await image.boundingBox();
  await a.locator('.pin-list__item', { hasText: 'Baie' }).first()
    .dragTo(mapView, { targetPosition: { x: box.x - (await mapView.boundingBox()).x + box.width * 0.7, y: box.y - (await mapView.boundingBox()).y + box.height * 0.6 } });
  await pin('Baie').waitFor({ timeout: 5000 });
  await sleep(300);
  pins = await pinsOf(map1);
  check(pins[baie] && Math.abs(pins[baie].x - 0.7) < 0.03 && Math.abs(pins[baie].y - 0.6) < 0.03, 'Baie épinglée par glisser-déposer');
  check(await a.locator('.pin-list__item.is-pinned', { hasText: 'Ys' }).count() === 1, 'Ys marqué « épinglé » dans la liste');

  // 3. Déplacement d'une épingle (mode « Placer les lieux »).
  const from = await pin('Ys').boundingBox();
  const to = await at(0.45, 0.5);
  await a.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await a.mouse.down();
  await a.mouse.move(from.x + from.width / 2 + 20, from.y + from.height / 2 + 10, { steps: 3 });
  await a.mouse.move(to.x, to.y, { steps: 8 });
  await a.mouse.up();
  await sleep(400);
  pins = await pinsOf(map1);
  check(Math.abs(pins[ys].x - 0.45) < 0.03 && Math.abs(pins[ys].y - 0.5) < 0.03, `épingle de Ys déplacée en (${pins[ys].x.toFixed(3)}, ${pins[ys].y.toFixed(3)})`);
  await editButton.click();   // « Terminer »
  check(!(await a.locator('.pin-list').isVisible()), 'mode « Placer les lieux » refermé');

  // 4. Zoom et déplacement de la carte (la liste refermée a élargi la carte : vue d'ensemble d'abord).
  await a.getByRole('button', { name: 'Toute la carte' }).click();
  const fit = await transform();
  await a.getByRole('button', { name: 'Zoomer', exact: true }).click();
  const zoomed = await transform();
  check(Math.abs(zoomed.k / fit.k - 1.5) < 0.01, `bouton Zoomer : échelle × 1,5 (${fit.k.toFixed(3)} > ${zoomed.k.toFixed(3)})`);
  const mbox = await mapView.boundingBox();
  await a.mouse.move(mbox.x + mbox.width / 2, mbox.y + mbox.height / 2);
  await a.mouse.wheel(0, -300);
  await sleep(200);
  const wheeled = await transform();
  check(wheeled.k > zoomed.k, `molette : zoom avant (${wheeled.k.toFixed(3)})`);
  await a.mouse.move(mbox.x + 200, mbox.y + 700);
  await a.mouse.down();
  await a.mouse.move(mbox.x + 260, mbox.y + 640, { steps: 6 });
  await a.mouse.up();
  const panned = await transform();
  check(Math.abs(panned.x - wheeled.x - 60) < 2 && Math.abs(panned.y - wheeled.y + 60) < 2, 'glisser le fond : la carte suit');
  await a.getByRole('button', { name: 'Dézoomer' }).click();
  await a.getByRole('button', { name: 'Toute la carte' }).click();
  const back = await transform();
  check(Math.abs(back.k - fit.k) < 1e-6 && Math.abs(back.x - fit.x) < 1, '« Toute la carte » : vue d’origine');

  // 5. Anneaux : sous-lieux de Ys, frères repliés, sous-sous-lieux, Échap.
  await pin('Ys').click();
  for (const name of ['Basse ville', 'Port', 'Haute ville']) await chip(name).waitFor({ timeout: 5000 });
  check(await a.locator('.map-chip').count() === 3, 'anneau de Ys : ses 3 sous-lieux');
  const panel = a.locator('.map-panel');
  await panel.locator('.map-panel__title', { hasText: 'Ys' }).waitFor();
  await panel.locator('h3', { hasText: 'Présents ici' }).waitFor({ timeout: 5000 });
  check(await panel.getByText('Hart').isVisible() && await panel.getByText('Gouverneur').isVisible(), 'fiche du lieu : présents (Hart, Gouverneur)');
  check(await panel.locator('a', { hasText: 'Ouvrir la fiche' }).getAttribute('href') === `#/c/${cid}/lieux/${ys}`, 'fiche du lieu : « Ouvrir la fiche »');
  await chip('Basse ville').click();
  await a.locator('.map-chip--more', { hasText: '+2 autres' }).waitFor({ timeout: 5000 });
  await chip('Taverne du Hareng').waitFor();
  await chip('Ruelles').waitFor();
  check(!(await chip('Port').isVisible()), 'Basse ville choisie : ses frères repliés en « +2 autres »');
  await panel.locator('.map-panel__title', { hasText: 'Basse ville' }).waitFor();
  check(await panel.locator('.map-panel__lineage button', { hasText: 'Ys' }).isVisible(), 'fiche : lieu parent dans le chemin');
  await chip('Taverne du Hareng').click();
  await chip('Cave').waitFor({ timeout: 5000 });
  check(await a.locator('svg.map-rings g').count() === 3, 'trois anneaux ouverts (Ys, Basse ville, Taverne)');
  await a.locator('.map-chip--more', { hasText: '+2 autres' }).click();
  await chip('Port').waitFor({ timeout: 5000 });
  check(!(await chip('Cave').isVisible()), '« +2 autres » : frères redéployés, anneaux plus profonds masqués');
  await a.keyboard.press('Escape');
  await chip('Cave').waitFor({ timeout: 5000 });
  check(!(await chip('Port').isVisible()), 'Échap : frères de nouveau repliés');
  await a.keyboard.press('Escape');
  await a.locator('.map-chip', { hasText: 'Cave' }).waitFor({ state: 'detached', timeout: 5000 });
  await panel.locator('.map-panel__title', { hasText: 'Basse ville' }).waitFor();
  check(true, 'Échap : dernier anneau refermé, fiche du parent');
  for (let i = 0; i < 3; i += 1) await a.keyboard.press('Escape');
  await a.locator('.map-chip').first().waitFor({ state: 'detached', timeout: 5000 });
  check(!(await panel.isVisible()), 'Échap jusqu’au bout : anneaux et fiche fermés');

  // 6. Pages d'un anneau : 12 îlots, 9 par page puis « +N ».
  await pin('Baie').click();
  await chip('Archipel').click();
  await chip('Îlot 01').waitFor({ timeout: 5000 });
  check(await chip('Îlot 09').isVisible() && !(await chip('Îlot 10').isVisible()), 'anneau de 12 sous-lieux : 9 affichés');
  await a.locator('.map-chip--more', { hasText: '+3' }).click();
  await chip('Îlot 12').waitFor({ timeout: 5000 });
  check(!(await chip('Îlot 01').isVisible()), '« +3 » : page suivante');
  await a.mouse.click(mbox.x + 40, mbox.y + mbox.height / 2);   // clic sur le fond : tout se referme
  await a.locator('.map-chip').first().waitFor({ state: 'detached', timeout: 5000 });
  check(true, 'clic sur le fond : anneaux fermés');

  // 7. Noms et aspect des épingles.
  check(await mapView.evaluate((el) => el.classList.contains('is-labels-hidden')), 'noms des épingles masqués par défaut');
  await a.getByRole('button', { name: 'Noms des épingles' }).click();
  check(!(await mapView.evaluate((el) => el.classList.contains('is-labels-hidden'))), 'noms des épingles affichés');
  await a.getByRole('button', { name: 'Aspect des épingles', exact: true }).click();
  await a.getByRole('menuitemradio', { name: /Discrètes/ }).click();
  await a.locator('.map-pin--hidden').first().waitFor({ timeout: 5000 });
  await sleep(300);
  check((await api(a, 'GET', `/api/maps/${map1}`)).pin_style === 'hidden', 'aspect « Discrètes » enregistré');
  await a.getByRole('button', { name: 'Aspect des épingles', exact: true }).click();
  await a.getByRole('menuitemradio', { name: /Pastilles/ }).click();
  await a.locator('.map-pin--dot').first().waitFor({ timeout: 5000 });

  // 8. Seconde carte qui représente Ys ; « Entrer », fil d'Ariane, changement de carte.
  await a.locator('.toolbar .btn', { has: a.locator('.btn__label', { hasText: /^Carte$/ }) }).click();
  const dialog2 = a.locator('.modal', { hasText: 'Nouvelle carte' });
  await dialog2.locator('#map-name').fill('Ville d’Ys');
  await dialog2.locator('#map-place').selectOption(String(ys));
  await dialog2.locator('input[type=file]').setInputFiles({ name: 'ys.png', mimeType: 'image/png', buffer: png(1200, 900) });
  await a.waitForFunction((n) => /\/cartes\/(\d+)$/.test(location.hash) && !location.hash.endsWith(`/${n}`), map1, { timeout: 30000 });
  const map2 = Number(a.url().match(/\/cartes\/(\d+)$/)[1]);
  await a.locator('.map-view[aria-label="Carte Ville d’Ys"]').waitFor({ timeout: 15000 });
  await a.locator('.map-trail').waitFor({ timeout: 10000 });
  check((await a.locator('.map-trail').innerText()).includes('Secteur'), 'carte de Ys : fil d’Ariane vers « Secteur »');
  await a.locator('.pin-list__item', { hasText: 'Taverne du Hareng' }).first().click();
  p = await at(0.5, 0.5);
  await a.mouse.click(p.x, p.y);
  await pin('Taverne du Hareng').waitFor();
  await editButton.click();   // « Terminer »
  await a.locator('select.maps__select').selectOption(String(map1));
  await a.waitForFunction((n) => location.hash.endsWith(`/cartes/${n}`), map1, { timeout: 10000 });
  await pin('Ys').waitFor({ timeout: 10000 });
  check(!(await pin('Taverne du Hareng').isVisible()), 'changement de carte (liste) : épingles de « Secteur »');
  await pin('Ys').click();
  await a.locator('.map-panel').getByRole('button', { name: /Entrer : Ville d’Ys/ }).click();
  await a.waitForFunction((n) => location.hash.endsWith(`/cartes/${n}`), map2, { timeout: 10000 });
  await pin('Taverne du Hareng').waitFor({ timeout: 10000 });
  check(true, '« Entrer » : carte qui représente le lieu');
  await a.locator('.map-trail button', { hasText: 'Secteur' }).click();
  await a.waitForFunction((n) => location.hash.includes(`/cartes/${n}`), map1, { timeout: 10000 });
  await pin('Ys').waitFor({ timeout: 10000 });
  await pin('Ys').dblclick();
  await a.waitForFunction((n) => location.hash.endsWith(`/cartes/${n}`), map2, { timeout: 10000 });
  check(true, 'double-clic sur l’épingle : entrer dans sa carte ; fil d’Ariane : retour');

  // 9. Arrivée depuis une fiche (?place=) : anneaux ouverts jusqu'au lieu.
  await a.goto(`${BASE}/#/c/${cid}/cartes?place=${taverne}`);
  await a.locator('.map-panel .map-panel__title', { hasText: 'Taverne du Hareng' }).waitFor({ timeout: 15000 });
  check(true, 'lien ?place= : fiche du lieu ouverte sur la carte la plus proche');

  // 10. Retrait d'une épingle, suppression d'une carte.
  await a.goto(`${BASE}/#/c/${cid}/cartes/${map1}`);
  await pin('Baie').waitFor({ timeout: 15000 });
  await editButton.click();   // « Placer les lieux »
  await pin('Baie').click();
  await a.locator('.map-panel').getByRole('button', { name: 'Retirer l’épingle' }).click();
  await pin('Baie').waitFor({ state: 'detached', timeout: 5000 });
  await sleep(300);
  check(!(await pinsOf(map1))[baie], 'épingle de Baie retirée');
  await editButton.click();   // « Terminer »
  await a.locator('select.maps__select').selectOption(String(map2));
  await a.waitForFunction((n) => location.hash.endsWith(`/cartes/${n}`), map2, { timeout: 10000 });
  await a.locator('.toolbar button[title^="Réglages de la carte"]').click();
  await a.locator('.modal').getByRole('button', { name: 'Supprimer la carte' }).click();
  await a.locator('.modal', { hasText: 'Supprimer la carte « Ville d’Ys »' }).getByRole('button', { name: 'Supprimer' }).click();
  await a.waitForFunction(() => /\/cartes$/.test(location.hash) || /\/cartes\/\d+$/.test(location.hash), null, { timeout: 10000 });
  await sleep(500);
  check((await api(a, 'GET', `/api/campaigns/${cid}/maps`)).maps.map((m) => m.id).join() === String(map1), 'carte « Ville d’Ys » supprimée');

  check(errors.length === 0, `aucune erreur de page${errors.length ? ` : ${errors.join(' | ')}` : ''}`);
  ok = true;
  console.log(`CARTES INTERACTIVES OK (compte ${USER}, mot de passe généré : ${PASSWORD})`);
} catch (e) {
  console.error('ÉCHEC :', e.message);
  for (const page of browser.contexts().flatMap((c) => c.pages())) {
    await page.screenshot({ path: path.join(SHOTS, `cartes-echec-${Date.now()}.png`) }).catch(() => {});
  }
  if (errors.length) console.error(errors.join('\n'));
} finally {
  await browser.close();
}
process.exit(ok ? 0 : 1);
