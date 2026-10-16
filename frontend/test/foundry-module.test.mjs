// Module Foundry « maze-sync » (backend/maze/foundry_module/scripts/maze-sync.mjs, audit M24) : logique de l'import
// sans Foundry. Le fichier est chargé tel qu'il est servi aux mondes, dans un contexte `node:vm` qui ne fournit que
// ce que son chargement exige (`foundry.applications.api.ApplicationV2`, `Hooks`) ; ses fonctions et classes de
// premier niveau (non exportées) sont lues dans ce contexte. Chaque test pose ensuite le strict nécessaire de
// `game`, `CONST`, `Folder`, `JournalEntry`, `Actor` et `document`, avec des documents simulés qui retiennent les
// écritures. Couvert : empreintes (valeurs figées : les changer ferait passer toutes les pages déjà importées pour
// modifiées dans Foundry), blocs secrets révélés, construction des pages (fiche, secrets du MJ, images), liens et
// images remplacés, dépôt des fichiers, choix des dossiers, rapprochement des documents, systèmes d'acteurs, textes du
// module (lang/fr.json et lang/en.json, `game.i18n` simulé comme celui de Foundry) en français et en anglais, langue
// des pages du monde (réglage du monde, langue retenue, mondes déjà importés) distincte de celle du poste du MJ.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { FRONTEND } from './helpers.mjs';

const MODULE_DIR = path.join(FRONTEND, '..', 'backend', 'maze', 'foundry_module');
const SOURCE = fs.readFileSync(path.join(MODULE_DIR, 'scripts', 'maze-sync.mjs'), 'utf8');
const MODULE_ID = 'maze-sync';

/** Textes du module à plat (« MAZE_SYNC.App.Title »), comme Foundry les charge depuis lang/<langue>.json. */
function langStrings(lang) {
  const flat = {};
  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      if (v && typeof v === 'object') walk(v, `${prefix}${k}.`);
      else flat[`${prefix}${k}`] = v;
    }
  };
  walk(JSON.parse(fs.readFileSync(path.join(MODULE_DIR, 'lang', `${lang}.json`), 'utf8')), '');
  return flat;
}

/** `game.i18n` de Foundry réduit à ce que le module utilise : lang, has, localize, format (clé absente : la clé). */
function makeI18n(lang = 'fr') {
  // Langue sans fichier dans le module (fr-CA, pt-BR…) : Foundry retombe sur l'anglais.
  const exists = (code) => fs.existsSync(path.join(MODULE_DIR, 'lang', `${code}.json`));
  const strings = langStrings(exists(lang) ? lang : 'en');
  return {
    lang,
    has: (key) => key in strings,
    localize: (key) => strings[key] ?? key,
    format: (key, data = {}) => (strings[key] ?? key).replace(/{[^}]+}/g, (k) => String(data[k.slice(1, -1)])),
  };
}

/** Valeurs venues du contexte du module (autre royaume JavaScript) : comparées en données simples. */
const plain = (value) => JSON.parse(JSON.stringify(value));

function getProperty(object, key) {
  return key.split('.').reduce((o, k) => o?.[k], object);
}

function setProperty(object, key, value) {
  const parts = key.split('.');
  let o = object;
  for (const k of parts.slice(0, -1)) o = (o[k] ??= {});
  o[parts.at(-1)] = value;
}

/** Charge le module dans un contexte neuf ; renvoie ses définitions internes et les crochets enregistrés. */
function loadModule() {
  if (/^\s*(import|export)\b/m.test(SOURCE)) {
    throw new Error('maze-sync.mjs contient import ou export : adapter loadModule (vm.SourceTextModule)');
  }
  const hooks = { once: {}, on: {} };
  const ctx = vm.createContext({
    console, File, Blob, URL,
    foundry: { applications: { api: { ApplicationV2: class {} } }, utils: { getProperty, setProperty } },
    Hooks: { once: (name, fn) => { hooks.once[name] = fn; }, on: (name, fn) => { hooks.on[name] = fn; } },
  });
  // Module ES = mode strict ; « use strict » sur la première ligne garde les numéros de ligne des erreurs.
  vm.runInContext(`"use strict";${SOURCE}`, ctx, { filename: 'maze-sync.mjs' });
  const inner = vm.runInContext(`({ MODULE_ID, ACTOR_SYSTEMS, hashText, normalizeSecrets, revealedIds, keepRevealed,
    contentHash, linkLabel, escapeHtml, MazeImporter, MazeClient, MazeImportApp, pageLanguage, knownLanguage,
    pageLanguageChoices })`, ctx);
  return { ctx, hooks, ...inner };
}

const mod = loadModule();

/* --- Monde Foundry simulé ------------------------------------------------------------------------------------- */

/** Collection de documents : tableau avec get(id), comme les collections de Foundry. */
function collection(items = []) {
  const list = [...items];
  list.get = (id) => list.find((d) => d.id === id);
  return list;
}

function applyChanges(target, changes) {
  for (const [key, value] of Object.entries(changes)) {
    if (key === '_id') continue;
    if (key.includes('.')) setProperty(target, key, value);
    else if (value && typeof value === 'object' && !Array.isArray(value) && target[key] && typeof target[key] === 'object') {
      applyChanges(target[key], value);
    } else target[key] = value;
  }
}

/** Réglages du module (game.settings) : valeurs par défaut de Foundry, écritures retenues dans `values`. */
function makeSettings(initial = {}) {
  const values = { url: '', token: '', lastCampaign: '', lastScenario: '', pageLanguage: 'auto', pageLanguageUsed: '',
    ...initial };
  return {
    values,
    get: (module, key) => {
      if (module !== MODULE_ID || !(key in values)) throw new Error(`réglage non enregistré : ${module}.${key}`);
      return values[key];
    },
    set: async (module, key, value) => { values[key] = value; return value; },
  };
}

function makeWorld({ system = 'dark-heresy-v1', maxDepth = 4, lang = 'fr', settings = {} } = {}) {
  let next = 1;
  const world = {
    writes: [],
    game: {
      journal: collection(), folders: collection(), scenes: collection(), actors: collection(),
      world: { id: 'monde' }, system: { id: system }, release: { generation: 13 }, i18n: makeI18n(lang),
      settings: makeSettings(settings),
    },
  };

  class FakeDoc {
    constructor(documentName, data) {
      this.documentName = documentName;
      this.id = data._id ?? `${documentName[0]}${next++}`;
      this.flags = {};
      this._stats = { createdTime: next };
      applyChanges(this, structuredClone(data));
      if (documentName === 'JournalEntry') this.pages = collection();
      if (documentName === 'Scene') Object.assign(this, { notes: collection() });
    }
    get uuid() { return `${this.documentName}.${this.id}`; }
    get folder() { return world.game.folders.get(this._folder) ?? null; }
    set folder(id) { this._folder = id; }
    get depth() { return this.folder ? this.folder.depth + 1 : 1; }
    async update(changes) {
      world.writes.push([this.documentName, 'update', this.name, plain(changes)]);
      applyChanges(this, changes);
      return this;
    }
    embedded(type) { return { JournalEntryPage: this.pages, Note: this.notes }[type]; }
    async createEmbeddedDocuments(type, list) {
      const created = list.map((data) => new FakeDoc(type, data));
      this.embedded(type).push(...created);
      world.writes.push([type, 'create', this.name, created.map((d) => d.name ?? d.text)]);
      return created;
    }
    async updateEmbeddedDocuments(type, list) {
      for (const changes of list) applyChanges(this.embedded(type).get(changes._id), changes);
      world.writes.push([type, 'update', this.name, plain(list)]);
    }
    async deleteEmbeddedDocuments(type, ids) {
      const coll = this.embedded(type);
      for (const id of ids) coll.splice(coll.findIndex((d) => d.id === id), 1);
      world.writes.push([type, 'delete', this.name, ids]);
    }
  }
  world.FakeDoc = FakeDoc;
  const create = (type, coll) => async (data) => {
    const doc = new FakeDoc(type, data);
    coll.push(doc);
    world.writes.push([type, 'create', doc.name]);
    return doc;
  };
  const g = world.game;
  world.globals = {
    game: g,
    CONST: { FOLDER_MAX_DEPTH: maxDepth, DOCUMENT_OWNERSHIP_LEVELS: { NONE: 0, OBSERVER: 2 },
      JOURNAL_ENTRY_PAGE_FORMATS: { HTML: 1 }, GRID_TYPES: { GRIDLESS: 0 }, DEFAULT_TOKEN: 'icons/svg/mystery-man.svg' },
    Folder: { implementation: { create: create('Folder', g.folders) } },
    Scene: { implementation: { create: create('Scene', g.scenes) } },
    JournalEntry: { implementation: { createDocuments: async (list) => Promise.all(list.map(create('JournalEntry', g.journal))) } },
    Actor: { implementation: {
      create: create('Actor', g.actors),
      updateDocuments: async (list) => list.map((c) => applyChanges(g.actors.get(c._id), c)),
    } },
    // decodeHtml : <textarea>.innerHTML puis .value décode les entités.
    document: { createElement: () => ({
      set innerHTML(html) {
        this.value = html.replace(/&(#\d+|amp|lt|gt|quot);/g, (all, e) =>
          (e[0] === '#' ? String.fromCharCode(Number(e.slice(1))) : { amp: '&', lt: '<', gt: '>', quot: '"' }[e]));
      },
    }) },
  };
  Object.assign(mod.ctx, world.globals);
  world.journal = (kind, id, extra = {}) => {
    const doc = new FakeDoc('JournalEntry', { name: `${kind} ${id}`, flags: { [MODULE_ID]: {
      instance: 'inst', campaign: 7, kind, id, created: `2026-01-0${id}`, ...extra } } });
    g.journal.push(doc);
    return doc;
  };
  return world;
}

// Noms créés dans le monde, renvoyés par l'export (`labels`, foundry.WORLD_LABELS) : mêmes textes que lang/<langue>.json
// du module (vérifié côté serveur par tests.test_foundry.WorldLabelsTest).
const WORLD_KEYS = ['Folder.Root', 'Folder.Maps', 'Folder.NoFaction', 'Folder.Removed', 'Kind.npc', 'Kind.faction',
  'Kind.place', 'Page.Sheet', 'Page.Secrets', 'Page.Maps', 'Page.ActorLink'];
const worldLabels = (lang) => Object.fromEntries(WORLD_KEYS.map((k) => [k, langStrings(lang)[`MAZE_SYNC.${k}`]]));

/** Export minimal d'une campagne (format maze-foundry/1 de backend/maze/foundry.py). */
function exportData() {
  const npc = (id, extra = {}) => ({ id, created: `2026-01-0${id}`, name: `PNJ ${id}`, title: '', status: 'vivant',
    group: null, cover: null, images: [], html: `<p>Fiche ${id}</p>`, secret_html: '', actor_bio: '', faction_names: [],
    ...extra });
  const place = (id, parent, hasChildren) => ({ id, created: `2026-01-0${id}`, name: `Lieu ${id}`, parent,
    kind: 'ville', has_children: hasChildren, cover: null, images: [], html: '', secret_html: '' });
  return {
    format: 'maze-foundry/1', instance: 'inst', campaign: { id: 7, name: 'Morvane' },
    place_kinds: [{ key: 'ville', label: 'Ville', icon: null }],
    files: { 5: { path: '7/portrait.webp' }, 6: { path: '7/carte d’Ebrane.jpg' } },
    factions: [{ id: 3, created: '2026-01-03', name: 'La Vigie', kind: '', color: '', cover: null, images: [],
      html: '', secret_html: '' }],
    npcs: [npc(1, { group: 3, cover: 5, images: [{ id: 5, caption: '' }, { id: 6, caption: 'Le port' }],
      actor_bio: '<p>Bio <a data-maze="faction:3">La Vigie</a></p>', faction_names: ['La Vigie'] }), npc(2)],
    // Arbre : 1 > 2 > 3 > 4 > 5 (chacun a un sous-lieu sauf 5).
    places: [place(1, null, true), place(2, 1, true), place(3, 2, true), place(4, 3, true), place(5, 4, false)],
    maps: [], scenarios: [],
  };
}

function importer(world, options = {}, data = exportData()) {
  const imp = new mod.MazeImporter({}, 7, { npc: true, faction: true, place: true, maps: false, actors: true,
    overwrite: false, ...options }, () => {});
  imp.data = data;
  imp.instance = data.instance;
  imp.entities = { npc: data.npcs, faction: data.factions, place: data.places };
  imp.actorNpcs = data.npcs;
  return imp;
}

/* --- Empreintes et blocs secrets --------------------------------------------------------------------------- */

test('empreintes figées (cyrb53) : celles des mondes déjà importés restent valables', () => {
  // Valeurs relevées sur la version 1.1.0 du module : un changement d'algorithme ferait passer toutes les pages
  // importées pour « modifiées dans Foundry » (plus aucune mise à jour sans « Remplacer »).
  assert.deepEqual(['', 'a', '<p>Fiche 1</p>', 'é€😀'].map((s) => mod.hashText(s)),
    ['wvjl67o803', '1guuo3poulb', '5hczmbyroy', 'azzwwek579']);
  assert.equal(mod.hashText(), mod.hashText(''));
  assert.notEqual(mod.hashText('<p>a</p>'), mod.hashText('<p>b</p>'));
});

test('blocs secrets : révélés ou non, même empreinte ; état révélé reporté sur le nouveau texte', () => {
  const hidden = '<p>x</p><section class="secret" id="secret-a"><p>s</p></section><section class="note" id="n">';
  const revealed = '<p>x</p><section class="secret revealed" id="secret-a"><p>s</p></section><section class="note" id="n">';
  assert.equal(mod.normalizeSecrets(revealed), hidden);
  assert.equal(mod.contentHash(revealed), mod.contentHash(hidden));
  assert.equal(mod.contentHash(null), mod.hashText(''));
  // Bloc secret sans id : laissé tel quel (impossible à suivre d'un import à l'autre).
  const noId = '<section class="secret revealed"><p>s</p></section>';
  assert.equal(mod.normalizeSecrets(noId), noId);
  assert.deepEqual([...mod.revealedIds(revealed + '<section class="revealed secret" id="secret-b">')],
    ['secret-a', 'secret-b']);
  const next = '<section class="secret" id="secret-a"><p>nouveau</p></section><section class="secret" id="secret-c">';
  assert.equal(mod.keepRevealed(next, mod.revealedIds(revealed)),
    '<section class="secret revealed" id="secret-a"><p>nouveau</p></section><section class="secret" id="secret-c">');
  assert.equal(mod.keepRevealed(next, new Set()), next);
});

/* --- Liens et images ------------------------------------------------------------------------------------------ */

test('render : mentions en liens @UUID, images de Maze remplacées par leur fichier dans le monde', () => {
  const world = makeWorld();
  const imp = importer(world);
  const npc = world.journal('npc', 2);
  imp.journals.set('npc:2', npc);
  imp.paths.set('7/portrait.webp', 'worlds/monde/maze/inst-7/portrait.webp');
  imp.paths.set('7/carte d’Ebrane.jpg', 'worlds/monde/maze/inst-7/a"b.jpg');
  const html = imp.render('<p><a data-maze="npc:2">Bob <em>&amp;</em> [co]{x}</a>, <a data-maze="place:9">Ailleurs</a></p>'
    + '<p><img data-maze-image="5" alt="Portrait"></p><p><img data-maze-image="6" alt=""></p>'
    + '<p><img data-maze-image="8" alt="absente"></p>');
  assert.equal(html, `<p>@UUID[${npc.uuid}]{Bob &amp; cox}, Ailleurs</p>`
    + '<p><img src="worlds/monde/maze/inst-7/portrait.webp" alt="Portrait"></p>'
    + '<p><img src="worlds/monde/maze/inst-7/a&#34;b.jpg" alt=""></p>');
  assert.equal(imp.render(null), '');
  // Libellé vide : nom du document ; caractères de la syntaxe retirés.
  assert.equal(imp.render('<a data-maze="npc:2"><b></b></a>'), `@UUID[${npc.uuid}]{npc 2}`);
  assert.equal(mod.linkLabel(' <a>{b}[c] & d '), 'abc &amp; d');
  assert.equal(mod.escapeHtml(`<"'&>`), '&#60;&#34;&#39;&#38;&#62;');
});

test('uploadFiles : fichiers déjà dans le monde réutilisés, les autres déposés, refus de Foundry signalé', async () => {
  const world = makeWorld();
  const uploads = [];
  mod.ctx.foundry.applications.apps = { FilePicker: { implementation: {
    createDirectory: async () => { throw new Error('existe'); },
    browse: async () => ({ files: ['worlds/monde/maze/inst-7/portrait.webp'] }),
    upload: async (source, dir, file) => {
      uploads.push([dir, file.name, file.type]);
      return file.name.endsWith('.jpg') ? false : { path: `${dir}/${file.name}` };
    },
  } } };
  const data = exportData();
  data.npcs[1].images = [{ id: 9, caption: '' }];
  data.files[9] = { path: '7/autre.webp' };
  const imp = importer(world, { actors: false }, data);
  imp.client = { file: async (p) => new Blob([p]) };
  await imp.uploadFiles();
  delete mod.ctx.foundry.applications.apps;
  assert.deepEqual(uploads, [['worlds/monde/maze/inst-7', 'carte d’Ebrane.jpg', 'image/jpeg'],
    ['worlds/monde/maze/inst-7', 'autre.webp', 'image/webp']]);
  assert.equal(imp.imagePath(5), 'worlds/monde/maze/inst-7/portrait.webp');   // déjà présent : pas redéposé
  assert.equal(imp.imagePath(9), 'worlds/monde/maze/inst-7/autre.webp');
  assert.equal(imp.imagePath(6), null);
  assert.equal(imp.imagePath(null), null);
  assert.deepEqual(plain(imp.report.errors), ['Image carte d’Ebrane.jpg : dépôt refusé par Foundry']);
});

/* --- Pages des journaux ---------------------------------------------------------------------------------------- */

test('fillJournal : page « Fiche » et page « Secrets (MJ) » réservée au MJ, empreintes posées', async () => {
  const world = makeWorld();
  const imp = importer(world);
  const doc = world.journal('npc', 2);
  const npc = { ...exportData().npcs[1], secret_html: '<p>Cultiste</p>' };
  const result = await imp.fillJournal(doc, 'npc', npc);
  assert.deepEqual(plain(result), { updated: true, skipped: [] });
  const [sheet, secrets] = doc.pages;
  assert.deepEqual(plain({ name: sheet.name, type: sheet.type, sort: sheet.sort, title: sheet.title, text: sheet.text }),
    { name: 'Fiche', type: 'text', sort: 0, title: { show: false, level: 1 }, text: { content: '<p>Fiche 2</p>', format: 1 } });
  assert.equal(sheet.ownership, undefined);
  assert.deepEqual(plain({ name: secrets.name, sort: secrets.sort, title: secrets.title, ownership: secrets.ownership }),
    { name: 'Secrets (MJ)', sort: 50, title: { show: true, level: 1 }, ownership: { default: 0 } });
  for (const [page, html] of [[sheet, '<p>Fiche 2</p>'], [secrets, '<p>Cultiste</p>']]) {
    assert.equal(page.flags[MODULE_ID].source, mod.hashText(html));
    assert.equal(page.flags[MODULE_ID].content, mod.contentHash(html));
  }
  // Sans secrets : pas de page « Secrets ».
  const other = world.journal('npc', 1);
  await imp.fillJournal(other, 'npc', exportData().npcs[1]);
  assert.deepEqual(other.pages.map((p) => p.name), ['Fiche']);
});

test('fillJournal : inchangé, changé dans Maze, modifié dans Foundry (gardé, ou remplacé avec l’option)', async () => {
  const world = makeWorld();
  const npc = { ...exportData().npcs[1], secret_html: '<section class="secret" id="secret-1"><p>s</p></section>' };
  const doc = world.journal('npc', 2);
  await importer(world).fillJournal(doc, 'npc', npc);

  world.writes.length = 0;
  assert.deepEqual(plain(await importer(world).fillJournal(doc, 'npc', npc)), { updated: false, skipped: [] });
  assert.deepEqual(world.writes, [], 'rien n’est réécrit');

  // Le MJ révèle le bloc secret dans Foundry : ce n'est pas une modification ; l'état révélé est gardé.
  const secrets = doc.pages.find((p) => p.name === 'Secrets (MJ)');
  secrets.text.content = '<section class="secret revealed" id="secret-1"><p>s</p></section>';
  const changed = { ...npc, html: '<p>Fiche 2, modifiée dans Maze</p>',
    secret_html: '<section class="secret" id="secret-1"><p>s2</p></section>' };
  assert.deepEqual(plain(await importer(world).fillJournal(doc, 'npc', changed)), { updated: true, skipped: [] });
  assert.equal(doc.pages[0].text.content, '<p>Fiche 2, modifiée dans Maze</p>');
  assert.equal(secrets.text.content, '<section class="secret revealed" id="secret-1"><p>s2</p></section>');

  // Modifiée dans Foundry, puis changée dans Maze : laissée telle quelle et signalée.
  doc.pages[0].text.content = '<p>Notes du MJ</p>';
  const again = { ...changed, html: '<p>Troisième version</p>' };
  assert.deepEqual(plain(await importer(world).fillJournal(doc, 'npc', again)), { updated: false, skipped: ['Fiche'] });
  assert.equal(doc.pages[0].text.content, '<p>Notes du MJ</p>');
  // Modifiée dans Foundry, inchangée dans Maze : ni réécrite, ni signalée.
  assert.deepEqual(plain(await importer(world).fillJournal(doc, 'npc', changed)), { updated: false, skipped: [] });
  // Option « Remplacer » : réécrite.
  await importer(world, { overwrite: true }).fillJournal(doc, 'npc', again);
  assert.equal(doc.pages[0].text.content, '<p>Troisième version</p>');
  // Secrets vidés dans Maze : page supprimée.
  await importer(world).fillJournal(doc, 'npc', { ...again, secret_html: '' });
  assert.deepEqual(doc.pages.map((p) => p.name), ['Fiche']);
});

test('pages d’images : une par image de la galerie, titre, légende et ordre ; retirées si l’image disparaît', async () => {
  const world = makeWorld();
  const imp = importer(world);
  imp.paths.set('7/portrait.webp', 'w/portrait.webp');
  imp.paths.set('7/carte d’Ebrane.jpg', 'w/carte.jpg');
  const doc = world.journal('npc', 1);
  const npc = exportData().npcs[0];
  assert.equal(await imp.syncImagePages(doc, npc), true);
  assert.deepEqual(plain(doc.pages.map((p) => [p.name, p.type, p.src, p.sort, p.image.caption, p.flags[MODULE_ID].role])),
    [['PNJ 1', 'image', 'w/portrait.webp', 100, '', 'image:5'], ['Le port', 'image', 'w/carte.jpg', 101, 'Le port', 'image:6']]);
  assert.equal(await imp.syncImagePages(doc, npc), false, 'inchangé : rien à écrire');
  // Image non téléchargée ou retirée de la galerie : sa page disparaît ; l'image restante devient la première.
  imp.paths.delete('7/portrait.webp');
  assert.equal(await imp.syncImagePages(doc, npc), true);
  assert.deepEqual(plain(doc.pages.map((p) => [p.name, p.sort, p.flags[MODULE_ID].role])), [['Le port', 101, 'image:6']]);
  assert.equal(imp.imageTitle({ name: 'Bob' }, { img: { caption: ' ' }, n: 2 }), 'Bob (3)');
});

test('sheetHtml : la fiche d’un lieu liste ses scènes', () => {
  const world = makeWorld();
  const imp = importer(world);
  const scene = new world.FakeDoc('Scene', { name: 'Ebrane' });
  imp.scenesByPlace.set(1, [scene]);
  assert.equal(imp.sheetHtml('place', { id: 1, html: '<p>Port</p>' }),
    `<p>Port</p><h2>Cartes</h2><ul><li><p>@UUID[${scene.uuid}]{Ebrane}</p></li></ul>`);
  assert.equal(imp.sheetHtml('npc', { id: 1, html: '<p>Port</p>' }), '<p>Port</p>');
});

/* --- Dossiers et rapprochement ------------------------------------------------------------------------------- */

test('dossiers : factions, PNJ par faction, lieux selon l’arbre dans la limite de profondeur de Foundry', async () => {
  const world = makeWorld({ maxDepth: 4 });
  const imp = importer(world);
  const data = imp.data;
  const pathOf = (folder) => (folder ? [...pathOf(folder.folder), folder.name] : []);
  const where = async (type, kind, entity) => pathOf(await imp.folderFor(type, kind, entity)).join(' / ');
  assert.equal(await where('JournalEntry', 'faction', data.factions[0]), 'Morvane - Factions');
  assert.equal(await where('JournalEntry', 'npc', data.npcs[0]), 'Morvane - PNJ / La Vigie');
  assert.equal(await where('JournalEntry', 'npc', data.npcs[1]), 'Morvane - PNJ / Sans faction');
  assert.equal(await where('Actor', 'npc', data.npcs[0]), 'Morvane - PNJ / La Vigie');
  // Lieu qui a des sous-lieux : son propre dossier ; sinon celui de son parent ; au-delà de 4 niveaux, le plus
  // profond possible.
  assert.equal(await where('JournalEntry', 'place', data.places[0]), 'Morvane - Lieux / Lieu 1');
  assert.equal(await where('JournalEntry', 'place', data.places[2]), 'Morvane - Lieux / Lieu 1 / Lieu 2 / Lieu 3');
  assert.equal(await where('JournalEntry', 'place', data.places[3]), 'Morvane - Lieux / Lieu 1 / Lieu 2 / Lieu 3');
  assert.equal(await where('JournalEntry', 'place', data.places[4]), 'Morvane - Lieux / Lieu 1 / Lieu 2 / Lieu 3');
  const journalFolders = world.game.folders.filter((f) => f.type === 'JournalEntry').length;
  assert.equal(journalFolders, 8);   // Factions, PNJ, La Vigie, Sans faction, Lieux, Lieu 1, Lieu 2, Lieu 3
  // Import suivant : dossiers retrouvés par leurs drapeaux, renommés ou déplacés si besoin, jamais recréés.
  data.factions[0].name = 'Ordre du Voile';
  const next = importer(world, {}, data);
  assert.equal(pathOf(await next.folderFor('JournalEntry', 'npc', data.npcs[0])).join(' / '), 'Morvane - PNJ / Ordre du Voile');
  assert.equal(world.game.folders.filter((f) => f.type === 'JournalEntry').length, journalFolders);
  assert.deepEqual(plain(world.game.folders.find((f) => f.name === 'Ordre du Voile').flags[MODULE_ID]),
    { instance: 'inst', campaign: 7, key: 'npc-group:3' });
});

test('rapprochement : identifiant réutilisé dans Maze = périmé, copie faite dans Foundry signalée', () => {
  const world = makeWorld();
  const imp = importer(world);
  const kept = world.journal('npc', 1);
  const copy = world.journal('npc', 1);
  const reused = world.journal('npc', 2, { created: '2025-12-31' });
  const gone = world.journal('npc', 4);
  const retired = world.journal('npc', 5, { removed: true });
  world.journal('npc', 6, { removed: true });   // déjà dans « Retirés de Maze » et toujours absente de Maze
  world.journal('faction', 3);
  const other = world.journal('npc', 1, { campaign: 8 });
  const ours = imp.ours(world.game.journal, 'npc');
  assert.ok(!ours.includes(other));
  const matched = imp.match(ours, [...exportData().npcs, { id: 5, created: '2026-01-05' }], 'PNJ');
  assert.deepEqual([...matched.keys()], [1, 5]);
  assert.equal(matched.get(1), kept);
  assert.deepEqual(plain(imp.report.duplicates), [`PNJ : ${copy.name}`]);
  // Périmés : rangés ensuite dans « Retirés de Maze », sauf ceux qui y sont déjà.
  assert.deepEqual([...imp.stale.JournalEntry], [reused, gone]);
  // Fiche réapparue : sortie de « Retirés de Maze ».
  const folder = new world.FakeDoc('Folder', { name: 'X' });
  assert.deepEqual(plain(imp.placement(retired, 'PNJ 5', folder)),
    { name: 'PNJ 5', folder: folder.id, [`flags.${MODULE_ID}.removed`]: false });
});

/* --- Acteurs ----------------------------------------------------------------------------------------------- */

test('acteurs : correspondance avec les systèmes Dark Heresy (champs remplis), autre système refusé', async () => {
  assert.deepEqual(plain(mod.ACTOR_SYSTEMS), {
    'dark-heresy-v1': { type: 'npc', bio: 'system.biography' },
    'dark-heresy': { type: 'npc', bio: 'system.notes', faction: 'system.faction' },
  });
  for (const [system, bioKey, faction] of [['dark-heresy-v1', 'biography', undefined], ['dark-heresy', 'notes', 'La Vigie']]) {
    const world = makeWorld({ system });
    const imp = importer(world);
    imp.paths.set('7/portrait.webp', 'w/portrait.webp');
    const journal = world.journal('npc', 1);
    const factionJournal = world.journal('faction', 3);
    imp.journals.set('npc:1', journal);
    imp.journals.set('faction:3', factionJournal);
    await imp.syncActors();
    const [actor, second] = world.game.actors;
    assert.equal(actor.type, 'npc');
    assert.equal(actor.system[bioKey],
      `<p>@UUID[${journal.uuid}]{Fiche de PNJ 1 (Maze)}</p><p>Bio @UUID[${factionJournal.uuid}]{La Vigie}</p>`);
    assert.equal(actor.system.faction, faction);
    assert.deepEqual(plain({ img: actor.img, token: actor.prototypeToken }),
      { img: 'w/portrait.webp', token: { name: 'PNJ 1', texture: { src: 'w/portrait.webp' } } });
    assert.equal(actor.flags[MODULE_ID].bio, mod.hashText(actor.system[bioKey]));
    assert.equal(actor.flags[MODULE_ID].bioStored, mod.hashText(actor.system[bioKey]));
    assert.equal(second.img, undefined, 'sans portrait : image par défaut de Foundry');
    assert.equal(second.system[bioKey], '');
    if (faction) assert.equal(second.system.faction, '');
  }
  const world = makeWorld({ system: 'dnd5e' });
  const imp = importer(world);
  await imp.syncActors();
  assert.equal(world.game.actors.length, 0);
  assert.deepEqual(plain(imp.report.errors), ['Acteurs : système « dnd5e » non pris en charge (journaux seulement).']);
});

test('acteurs : portrait et biographie remplacés seulement s’ils n’ont pas été changés dans Foundry', async () => {
  const world = makeWorld();
  const first = importer(world);
  first.paths.set('7/portrait.webp', 'w/portrait.webp');
  await first.syncActors();
  const actor = world.game.actors[0];

  // Nouveau portrait et nouvelle biographie dans Maze, rien touché dans Foundry : remplacés.
  const data = exportData();
  data.npcs[0].actor_bio = '<p>Nouvelle bio</p>';
  const second = importer(world, {}, data);
  second.paths.set('7/portrait.webp', 'w/portrait-2.webp');
  await second.syncActors();
  assert.equal(actor.img, 'w/portrait-2.webp');
  assert.equal(actor.prototypeToken.texture.src, 'w/portrait-2.webp');
  assert.equal(actor.system.biography, '<p>Nouvelle bio</p>');
  assert.equal(second.report.updated, 1);

  // Changés dans Foundry : gardés ; la biographie est signalée.
  actor.img = 'w/choisi-par-le-mj.webp';
  actor.system.biography = '<p>Notes du MJ</p>';
  data.npcs[0].actor_bio = '<p>Bio 3</p>';
  const third = importer(world, {}, data);
  third.paths.set('7/portrait.webp', 'w/portrait-3.webp');
  await third.syncActors();
  assert.equal(actor.img, 'w/choisi-par-le-mj.webp');
  assert.equal(actor.system.biography, '<p>Notes du MJ</p>');
  assert.deepEqual(plain(third.report.skipped), ['Acteur : PNJ 1 (biographie)']);
  // Option « Remplacer » : biographie remplacée (le portrait choisi par le MJ reste).
  const fourth = importer(world, { overwrite: true }, data);
  fourth.paths.set('7/portrait.webp', 'w/portrait-3.webp');
  await fourth.syncActors();
  assert.equal(actor.system.biography, '<p>Bio 3</p>');
  assert.equal(actor.img, 'w/choisi-par-le-mj.webp');

  // Acteur sans portrait posé par l'import (drapeau `img` absent) : remplacé seulement s'il a l'image par défaut.
  delete actor.flags[MODULE_ID].img;
  const fifth = importer(world, {}, data);
  fifth.paths.set('7/portrait.webp', 'w/portrait-5.webp');
  await fifth.syncActors();
  assert.equal(actor.img, 'w/choisi-par-le-mj.webp');
  actor.img = 'icons/svg/mystery-man.svg';
  await fifth.syncActors();
  assert.equal(actor.img, 'w/portrait-5.webp');
});

/* --- Chargement dans Foundry ----------------------------------------------------------------------------------- */

test('chargement : réglages du client, menu réservé au MJ, API du module', () => {
  const settings = [];
  const menus = [];
  mod.ctx.game = { settings: { register: (...a) => settings.push(a), registerMenu: (...a) => menus.push(a) },
    modules: new Map([[MODULE_ID, { languages: new Set([{ lang: 'fr', path: 'lang/fr.json' }, { lang: 'en', path: 'lang/en.json' }]) }]]),
    i18n: makeI18n('fr') };
  mod.hooks.once.init();
  // Adresse, jeton et derniers choix : poste du MJ ; langue des pages : monde (la même pour tous les MJ).
  assert.deepEqual(settings.map(([m, key, o]) => [m, key, o.scope, o.config]), [
    ...['url', 'token', 'lastCampaign', 'lastScenario'].map((k) => [MODULE_ID, k, 'client', false]),
    [MODULE_ID, 'pageLanguage', 'world', true], [MODULE_ID, 'pageLanguageUsed', 'world', false]]);
  const language = settings.find(([, key]) => key === 'pageLanguage')[2];
  assert.equal(language.default, 'auto');
  assert.deepEqual(plain(language.choices), { auto: 'MAZE_SYNC.Settings.PageLanguage.Auto', fr: 'Français', en: 'English' });
  assert.deepEqual([language.name, language.hint],
    ['MAZE_SYNC.Settings.PageLanguage.Name', 'MAZE_SYNC.Settings.PageLanguage.Hint']);
  assert.equal(menus[0][2].restricted, true);
  // Clés de traduction, traduites par Foundry à l'affichage (pas encore chargées à « init »).
  assert.deepEqual([menus[0][2].name, menus[0][2].label, menus[0][2].hint],
    ['MAZE_SYNC.Menu.Name', 'MAZE_SYNC.Menu.Label', 'MAZE_SYNC.Menu.Hint']);
  assert.equal(menus[0][2].type, mod.MazeImportApp);
  mod.hooks.once.ready();
  const api = mod.ctx.game.modules.get(MODULE_ID).api;
  assert.equal(api.MazeImporter, mod.MazeImporter);
  assert.equal(api.MazeClient, mod.MazeClient);
  assert.equal(typeof mod.hooks.on.renderJournalDirectory, 'function');
  const manifest = JSON.parse(fs.readFileSync(path.join(MODULE_DIR, 'module.json'), 'utf8'));
  assert.deepEqual(manifest.esmodules, ['scripts/maze-sync.mjs']);
  assert.equal(manifest.id, MODULE_ID);
  assert.deepEqual(manifest.languages.map((l) => [l.lang, l.path]), [['fr', 'lang/fr.json'], ['en', 'lang/en.json']]);
  for (const l of manifest.languages) assert.ok(fs.existsSync(path.join(MODULE_DIR, l.path)), l.path);
});

test('MazeClient : adresse nettoyée, jeton envoyé, erreurs de Maze traduites', async () => {
  mod.ctx.game = { i18n: makeI18n('fr') };
  const client = new mod.MazeClient(' https://maze.example.org/// ', ' mzf_abc ');
  assert.equal(client.base, 'https://maze.example.org');
  const calls = [];
  const responses = { '/api/foundry/v1/campaigns': { status: 200, body: { format: 'maze-foundry/1', campaigns: [{ id: 7 }] } } };
  mod.ctx.fetch = async (url, options) => {
    calls.push([url, options.headers.Authorization, options.cache]);
    const r = responses[url.replace(client.base, '').replace(/\?lang=\w+$/, '')] ?? { status: 404 };
    return { ok: r.status < 300, status: r.status, json: async () => r.body, blob: async () => new Blob(['x']) };
  };
  assert.deepEqual(plain(await client.campaigns()), [{ id: 7 }]);
  assert.deepEqual(calls[0], ['https://maze.example.org/api/foundry/v1/campaigns', 'Bearer mzf_abc', 'no-store']);
  await client.file('7/carte d’Ebrane.jpg').catch(() => {});
  assert.equal(calls[1][0], 'https://maze.example.org/api/foundry/v1/media/7/carte%20d%E2%80%99Ebrane.jpg');
  await assert.rejects(client.exportCampaign(9), /Introuvable dans Maze/);
  responses['/api/foundry/v1/campaigns/9/export'] = { status: 401 };
  await assert.rejects(client.exportCampaign(9), /Jeton refusé/);
  responses['/api/foundry/v1/campaigns/9/export'] = { status: 200, body: { format: 'maze-foundry/2' } };
  await assert.rejects(client.exportCampaign(9), /Format inattendu \(maze-foundry\/2\)/);
  await assert.rejects(new mod.MazeClient('maze.example.org', 't').campaigns(), /Adresse de Maze invalide/);
  // Langue du monde envoyée à Maze, qui compose les pages dans cette langue.
  assert.equal(calls.at(-1)[0], 'https://maze.example.org/api/foundry/v1/campaigns/9/export?lang=fr');
  delete mod.ctx.fetch;
});

/* --- Textes du module : français et anglais ------------------------------------------------------------------ */

test('lang/fr.json et lang/en.json : mêmes clés, mêmes paramètres, toutes les clés du module présentes', () => {
  const fr = langStrings('fr');
  const en = langStrings('en');
  assert.deepEqual(Object.keys(en).sort(), Object.keys(fr).sort());
  const params = (text) => [...text.matchAll(/{(\w+)}/g)].map((m) => m[1]).sort();
  for (const key of Object.keys(fr)) {
    assert.ok(key.startsWith('MAZE_SYNC.'), key);
    assert.ok(fr[key].trim() && en[key].trim(), `${key} : texte vide`);
    assert.deepEqual(params(en[key]), params(fr[key]), `${key} : paramètres`);
  }
  // Clés écrites telles quelles dans le module (L("…"), Ln("…"), "MAZE_SYNC.…"), et clés composées.
  const used = new Set();
  for (const m of SOURCE.matchAll(/\bLn?\("([\w.]+)"/g)) used.add(m[1]);
  for (const m of SOURCE.matchAll(/T\("([\w.]+)"/g)) used.add(m[1]);
  for (const m of SOURCE.matchAll(/\blabel\("([\w.]+)"/g)) used.add(m[1]);
  for (const m of SOURCE.matchAll(/"MAZE_SYNC\.([\w.]+)"/g)) used.add(m[1]);
  for (const kind of ['npc', 'faction', 'place', 'map', 'actor']) used.add(`Kind.${kind}`);
  for (const status of ['brouillon', 'pret', 'en_cours', 'joue']) used.add(`Status.${status}`);
  assert.ok(used.size > 60, `${used.size} clés trouvées`);
  const known = (key) => `MAZE_SYNC.${key}` in fr || `MAZE_SYNC.${key}.other` in fr;
  assert.deepEqual([...used].filter((key) => !known(key)), []);
  // Plus aucun texte français en dur hors commentaires (chaînes et gabarits avec des lettres accentuées).
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\s\/\/ .*$/gm, '');
  const strings = [...code.matchAll(/(["'`])((?:\\.|(?!\1)[^\\\n])*)\1/g)].map((m) => m[2]);
  assert.deepEqual(strings.filter((str) => /[éèàùçêâîôû«»]/.test(str) && !str.startsWith('MAZE_SYNC')), []);
});

test('repli sans libellés de l’export : dossiers, pages et acteurs dans la langue de Foundry', async () => {
  // Export d'une version de Maze qui ne renvoie pas `labels` : textes du module dans la langue du poste.
  const world = makeWorld({ lang: 'en' });
  const imp = importer(world);
  const data = imp.data;
  const pathOf = (folder) => (folder ? [...pathOf(folder.folder), folder.name] : []);
  const where = async (kind, entity) => pathOf(await imp.folderFor('JournalEntry', kind, entity)).join(' / ');
  assert.equal(await where('faction', data.factions[0]), 'Morvane - Factions');
  assert.equal(await where('npc', data.npcs[0]), 'Morvane - NPCs / La Vigie');
  assert.equal(await where('npc', data.npcs[1]), 'Morvane - NPCs / No faction');
  assert.equal(await where('place', data.places[1]), 'Morvane - Places / Lieu 1 / Lieu 2');

  const doc = world.journal('npc', 2);
  await imp.fillJournal(doc, 'npc', { ...data.npcs[1], secret_html: '<p>Cultist</p>' });
  assert.deepEqual(doc.pages.map((p) => p.name), ['Sheet', 'Secrets (GM)']);
  doc.pages[0].text.content = '<p>GM notes</p>';
  const result = await importer(world).fillJournal(doc, 'npc', { ...data.npcs[1], html: '<p>v2</p>', secret_html: '<p>Cultist</p>' });
  assert.deepEqual(plain(result.skipped), ['Sheet']);

  const scene = new world.FakeDoc('Scene', { name: 'Ebrane' });
  imp.scenesByPlace.set(1, [scene]);
  assert.equal(imp.sheetHtml('place', { id: 1, html: '' }), `<h2>Maps</h2><ul><li><p>@UUID[${scene.uuid}]{Ebrane}</p></li></ul>`);

  const journal = world.journal('npc', 1);
  imp.journals.set('npc:1', journal);
  await imp.syncActors();
  assert.match(world.game.actors[0].system.biography, /^<p>@UUID\[JournalEntry\.\w+\]{PNJ 1's sheet \(Maze\)}<\/p>/);
  const other = importer(makeWorld({ system: 'dnd5e', lang: 'en' }));
  await other.syncActors();
  assert.deepEqual(plain(other.report.errors), ['Actors: system “dnd5e” not supported (journals only).']);
  const copies = importer(world);
  copies.match([world.journal('npc', 1), world.journal('npc', 1)], data.npcs, 'NPCs');
  assert.equal(plain(copies.report.duplicates).length, 1);
  assert.match(copies.report.duplicates[0], /^NPCs: npc 1$/);
});

test('anglais : fenêtre, rapport et erreurs de Maze ; langue envoyée avec l’export', async () => {
  const world = makeWorld({ lang: 'en' });
  mod.ctx.game.settings = { get: () => '' };
  const app = new mod.MazeImportApp();
  const options = app.scenarioOptions({ id: 7, scenarios: [{ id: 1, title: 'Prologue', status: 'en_cours' },
    { id: 2, title: '', status: 'autre' }] });
  assert.equal(options, '<option value="">Whole campaign</option><option value="1">Prologue (in progress)</option>'
    + '<option value="2">Untitled</option>');
  const report = app.reportHtml({ created: 1, updated: 2, unchanged: 0, removed: 3, skipped: ['NPCs: Bob (Sheet)'],
    duplicates: [], errors: [] });
  assert.match(report, /<strong>Done:<\/strong> 1 created, 2 updated, 0 unchanged, 3 removed from Maze\./);
  assert.match(report, /<summary>Changed in Foundry, left as they are \(1\)<\/summary>/);
  assert.doesNotMatch(report, /Errors/);
  world.game.i18n = makeI18n('fr');   // même rapport en français : pluriels de la langue (0 et 1 au singulier)
  assert.match(app.reportHtml({ created: 1, updated: 1, unchanged: 2, removed: 0, skipped: [], duplicates: [],
    errors: ['x'] }), /<strong>Terminé :<\/strong> 1 créé, 1 mis à jour, 2 inchangés\.[\s\S]*Erreurs \(1\)/);

  mod.ctx.game = { i18n: makeI18n('en') };
  const client = new mod.MazeClient('https://maze.example.org', 'mzf_abc');
  const urls = [];
  mod.ctx.fetch = async (url) => {
    urls.push(url);
    return { ok: false, status: url.includes('/export') ? 401 : 503 };
  };
  await assert.rejects(client.exportCampaign(9), /^Error: Token refused by Maze \(revoked or copied incorrectly\)\.$/);
  assert.equal(urls[0], 'https://maze.example.org/api/foundry/v1/campaigns/9/export?lang=en');
  await assert.rejects(client.campaigns(), /^Error: Maze answered 503\.$/);
  await assert.rejects(new mod.MazeClient('maze', 't').campaigns(), /Invalid Maze address/);
  delete mod.ctx.fetch;
});

/* --- Langue des pages du monde ---------------------------------------------------------------------------------- */

test('langue des pages : réglage du monde, langue retenue, monde déjà importé, sinon langue du poste', () => {
  // Monde neuf, réglage « Automatique » : langue du poste ramenée aux langues du module (anglais à défaut).
  for (const [client, expected] of [['en', 'en'], ['fr', 'fr'], ['fr-CA', 'fr'], ['en_GB', 'en'], ['pt-BR', 'en']]) {
    makeWorld({ lang: client });
    assert.equal(mod.pageLanguage(), expected, client);
  }
  // Langue retenue au premier import : elle l'emporte sur le poste du MJ qui importe.
  makeWorld({ lang: 'en', settings: { pageLanguageUsed: 'fr' } });
  assert.equal(mod.pageLanguage(), 'fr');
  makeWorld({ lang: 'fr', settings: { pageLanguageUsed: 'en' } });
  assert.equal(mod.pageLanguage(), 'en');
  // Monde importé par une version d'avant ce réglage (documents marqués, aucune langue retenue) : français, langue de
  // ces versions, pour que les empreintes des pages restent valables. Dossier seul, scène ou acteur : de même.
  let world = makeWorld({ lang: 'en' });
  world.journal('npc', 1);
  assert.equal(mod.pageLanguage(), 'fr');
  for (const coll of ['folders', 'scenes', 'actors']) {
    world = makeWorld({ lang: 'en' });
    world.game[coll].push(new world.FakeDoc('Folder', { name: 'x', flags: { [MODULE_ID]: { instance: 'i', campaign: 1 } } }));
    assert.equal(mod.pageLanguage(), 'fr', coll);
  }
  world = makeWorld({ lang: 'en' });
  world.game.journal.push(new world.FakeDoc('JournalEntry', { name: 'Notes du MJ', flags: { autre: {} } }));
  assert.equal(mod.pageLanguage(), 'en');   // documents d'autres modules : ignorés
  // Réglage explicite : il l'emporte sur tout.
  world = makeWorld({ lang: 'fr', settings: { pageLanguage: 'en', pageLanguageUsed: 'fr' } });
  world.journal('npc', 1);
  assert.equal(mod.pageLanguage(), 'en');
  makeWorld({ lang: 'en', settings: { pageLanguage: 'fr' } });
  assert.equal(mod.pageLanguage(), 'fr');
  // Avant l'enregistrement des réglages (ou sans Foundry) : langue du poste.
  mod.ctx.game = { i18n: makeI18n('en') };
  assert.equal(mod.pageLanguage(), 'en');
  assert.equal(mod.knownLanguage('EN-us'), 'en');
  assert.equal(mod.knownLanguage(''), 'en');
});

/** Import complet (run) avec un client de Maze simulé, qui répond dans la langue demandée. */
async function runImport(world, { exportLocale } = {}) {
  const asked = [];
  const client = {
    exportCampaign: async (id, lang) => {
      asked.push(lang);
      const data = exportData();
      for (const n of data.npcs) Object.assign(n, { images: [], cover: null });
      const locale = exportLocale ?? lang;
      return { ...data, locale, labels: worldLabels(locale) };
    },
  };
  const lines = [];
  const report = await new mod.MazeImporter(client, 7, { npc: true, faction: true, place: true, maps: false,
    actors: false, overwrite: false }, (line) => lines.push(line)).run();
  return { asked, lines, report };
}

const folderPath = (folder) => (folder ? [...folderPath(folder.folder), folder.name] : []);

test('import, poste en anglais et monde en français : pages et dossiers en français, interface en anglais', async () => {
  const world = makeWorld({ lang: 'en', settings: { pageLanguageUsed: 'fr' } });
  const { asked, lines } = await runImport(world);
  assert.deepEqual(asked, ['fr']);
  assert.equal(lines[0], 'Reading the campaign from Maze…');                      // interface : langue du poste
  const npc = world.game.journal.find((j) => j.name === 'PNJ 2');
  assert.deepEqual(npc.pages.map((p) => p.name), ['Fiche']);                     // monde : langue des pages
  assert.equal(folderPath(npc.folder).join(' / '), 'Morvane - PNJ / Sans faction');
  assert.equal(folderPath(world.game.journal.find((j) => j.name === 'La Vigie').folder).join(' / '), 'Morvane - Factions');
  assert.equal(world.game.settings.values.pageLanguageUsed, 'fr');

  // Second import par un MJ dont Foundry est en anglais : rien ne bascule (mêmes noms, pages inchangées).
  const before = world.game.folders.map((f) => f.name);
  world.writes.length = 0;
  const again = await runImport(world);
  assert.deepEqual(again.asked, ['fr']);
  assert.deepEqual(world.game.folders.map((f) => f.name), before);
  assert.equal(again.report.updated, 0);
  assert.deepEqual(plain(again.report.skipped), []);
  assert.deepEqual(world.writes.filter(([, op]) => op === 'update'), []);
});

test('import : monde déjà importé sans langue retenue > français, retenu ; monde neuf > langue du poste', async () => {
  // Monde importé par la version 1.1 (pages en français, aucune langue retenue), MJ en anglais.
  let world = makeWorld({ lang: 'en' });
  world.journal('npc', 2);
  let result = await runImport(world);
  assert.deepEqual(result.asked, ['fr']);
  assert.equal(world.game.settings.values.pageLanguageUsed, 'fr');
  assert.deepEqual(world.game.journal.find((j) => j.name === 'PNJ 2').pages.map((p) => p.name), ['Fiche']);

  // Monde neuf, MJ en anglais : anglais, retenu pour les MJ suivants (même en français).
  world = makeWorld({ lang: 'en' });
  result = await runImport(world);
  assert.deepEqual(result.asked, ['en']);
  assert.equal(world.game.settings.values.pageLanguageUsed, 'en');
  assert.deepEqual(world.game.journal.find((j) => j.name === 'PNJ 2').pages.map((p) => p.name), ['Sheet']);
  assert.equal(folderPath(world.game.journal.find((j) => j.name === 'PNJ 2').folder).join(' / '),
    'Morvane - NPCs / No faction');
  world.game.i18n = makeI18n('fr');
  result = await runImport(world);
  assert.deepEqual(result.asked, ['en']);
  assert.equal(result.lines[0], 'Lecture de la campagne dans Maze…');

  // Langue que le serveur ne connaît pas : celle qu'il a employée (MAZE_DEFAULT_LOCALE) est retenue.
  world = makeWorld({ lang: 'en' });
  result = await runImport(world, { exportLocale: 'fr' });
  assert.deepEqual(result.asked, ['en']);
  assert.equal(world.game.settings.values.pageLanguageUsed, 'fr');
});

test('import, réglage explicite du monde : il l’emporte, la langue retenue suit', async () => {
  const world = makeWorld({ lang: 'fr', settings: { pageLanguage: 'en', pageLanguageUsed: 'fr' } });
  world.journal('npc', 2);
  const { asked } = await runImport(world);
  assert.deepEqual(asked, ['en']);
  assert.equal(world.game.settings.values.pageLanguageUsed, 'en');
  // Nouvelles pages et dossiers dans la langue choisie ; rapport dans celle du poste.
  assert.equal(folderPath(world.game.journal.find((j) => j.name === 'PNJ 1').folder).join(' / '), 'Morvane - NPCs / La Vigie');
  // Client de Maze : langue des pages envoyée par défaut, pas celle du poste.
  const urls = [];
  mod.ctx.fetch = async (url) => { urls.push(url); return { ok: false, status: 401 }; };
  await assert.rejects(new mod.MazeClient('https://maze.example.org', 't').exportCampaign(9), /Jeton refusé/);
  assert.equal(urls[0], 'https://maze.example.org/api/foundry/v1/campaigns/9/export?lang=en');
  delete mod.ctx.fetch;
});
