/**
 * Maze - import des fiches (module Foundry VTT 13 et 14, servi par Maze sous /foundry/).
 *
 * Lit l'export d'une campagne de Maze (GET /api/foundry/v1/campaigns/<id>/export, jeton de
 * Administration > Foundry VTT) et crée ou met à jour dans le monde :
 *  - un journal par PNJ, faction et lieu : page « Fiche », page « Secrets » réservée au MJ (secrets, liens
 *    secrets, notes du MJ) et une page par image de la galerie ; rangés dans des dossiers (PNJ par faction,
 *    lieux selon leur arborescence) ;
 *  - une scène par carte interactive, avec une note par lieu épinglé ;
 *  - un acteur par PNJ (systèmes Dark Heresy), sans toucher à ses caractéristiques.
 * Rien ne repart vers Maze. Import limité à un scénario au choix : ses fiches (citées dans lui et ses pages,
 * ajouts et retraits de sa carte), éventuellement leurs voisines, et les cartes de ses lieux.
 *
 * Chaque document importé porte `flags.maze-sync` ({instance, campaign, kind, id, created}) ; chaque page
 * de texte porte {role, source, content} : `source` = empreinte du HTML écrit, `content` = empreinte du HTML
 * tel que Foundry l'a enregistré (blocs secrets révélés ramenés à leur forme cachée). Un nouvel import met à
 * jour ce qui a changé dans Maze et laisse de côté ce qui a été modifié dans Foundry depuis le dernier import
 * (sauf si l'option « remplacer » est cochée) ; une fiche disparue de Maze est rangée dans « Retirés de Maze ».
 */

const MODULE_ID = "maze-sync";
const APP_ID = "maze-sync-import";
const API = "/api/foundry/v1";
const FORMAT = "maze-foundry/1";
const KINDS = ["npc", "faction", "place"];
const KIND_LABEL = { npc: "PNJ", faction: "Factions", place: "Lieux" };
const KIND_DATA = { npc: "npcs", faction: "factions", place: "places" };
const SCENARIO_STATUS = { brouillon: "brouillon", pret: "prêt", en_cours: "en cours", joue: "joué" };
const DEFAULT_NOTE_ICON = "icons/svg/book.svg";

// Acteurs : systèmes pris en charge (type d'acteur et champs remplis ; le reste de la fiche n'est jamais touché).
const ACTOR_SYSTEMS = {
  "dark-heresy-v1": { type: "npc", bio: "system.biography" },
  "dark-heresy": { type: "npc", bio: "system.notes", faction: "system.faction" }
};

/* ---------------------------------------------------------------------------------------------- */
/* Outils                                                                                          */
/* ---------------------------------------------------------------------------------------------- */

/** Empreinte courte d'un texte (cyrb53). */
function hashText(text = "") {
  let h1 = 0xdeadbeef ^ text.length, h2 = 0x41c6ce57 ^ text.length;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

// Blocs secrets : Foundry ajoute la classe « revealed » quand le MJ en révèle un. Ce n'est pas une
// modification du texte : les empreintes se calculent sur la forme cachée, et l'état révélé est reporté
// sur le nouveau texte à chaque mise à jour.
const SECTION_TAG = /<section\b[^>]*>/g;

function secretState(tag) {
  const cls = tag.match(/\bclass="([^"]*)"/)?.[1]?.split(/\s+/) ?? [];
  const id = tag.match(/\bid="([^"]*)"/)?.[1] ?? null;
  return { secret: cls.includes("secret"), revealed: cls.includes("revealed"), id };
}

function normalizeSecrets(html = "") {
  return html.replace(SECTION_TAG, (tag) => {
    const s = secretState(tag);
    return s.secret && s.id ? `<section class="secret" id="${s.id}">` : tag;
  });
}

function revealedIds(html = "") {
  const ids = new Set();
  for (const [tag] of html.matchAll(SECTION_TAG)) {
    const s = secretState(tag);
    if (s.secret && s.revealed && s.id) ids.add(s.id);
  }
  return ids;
}

function keepRevealed(html, ids) {
  if (!ids.size) return html;
  return html.replace(/<section class="secret" id="([^"]+)">/g,
    (tag, id) => (ids.has(id) ? `<section class="secret revealed" id="${id}">` : tag));
}

const contentHash = (html) => hashText(normalizeSecrets(html ?? ""));

function decodeHtml(text) {
  const area = document.createElement("textarea");
  area.innerHTML = text;
  return area.value;
}

const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/**
 * Libellé d'un lien `@UUID[...]{libellé}` : texte brut, sans les caractères de la syntaxe ni balise, et
 * « & » doublement protégé (Foundry relit le libellé comme texte puis l'insère comme HTML).
 */
function linkLabel(text) {
  return String(text ?? "").replace(/[{}[\]<>]/g, "").replace(/&/g, "&amp;").trim();
}

const uuidLink = (doc, label) => `@UUID[${doc.uuid}]{${linkLabel(label) || linkLabel(doc.name)}}`;

const flagOf = (doc) => doc?.flags?.[MODULE_ID] ?? null;
const maxDepth = () => CONST.FOLDER_MAX_DEPTH ?? 4;
const filePicker = () => foundry.applications?.apps?.FilePicker?.implementation ?? globalThis.FilePicker;
const createdTime = (doc) => doc?._stats?.createdTime ?? 0;

async function inBatches(items, size, fn) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(...(await fn(items.slice(i, i + size))));
  return out;
}

async function pool(items, limit, fn) {
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const item = items[next++];
      await fn(item);
    }
  });
  await Promise.all(workers);
}

/* ---------------------------------------------------------------------------------------------- */
/* Client de l'API de Maze                                                                         */
/* ---------------------------------------------------------------------------------------------- */

class MazeClient {
  constructor(url, token) {
    this.base = String(url || "").trim().replace(/\/+$/, "");
    this.token = String(token || "").trim();
  }

  async request(path) {
    if (!/^https?:\/\//i.test(this.base)) throw new Error("Adresse de Maze invalide (https://…)");
    let response;
    try {
      response = await fetch(this.base + path, { headers: { Authorization: `Bearer ${this.token}` }, cache: "no-store" });
    } catch (err) {
      throw new Error(`Maze injoignable (${this.base}) : adresse, certificat ou CORS. ${err.message ?? err}`);
    }
    if (response.status === 401) throw new Error("Jeton refusé par Maze (révoqué ou mal copié).");
    if (response.status === 404) throw new Error("Introuvable dans Maze (campagne non ouverte à ce jeton ?).");
    if (!response.ok) throw new Error(`Maze a répondu ${response.status}.`);
    return response;
  }

  async json(path) {
    const data = await (await this.request(path)).json();
    if (data.format !== FORMAT) throw new Error(`Format inattendu (${data.format}) : mettre à jour le module.`);
    return data;
  }

  async campaigns() {
    return (await this.json(`${API}/campaigns`)).campaigns;
  }

  async exportCampaign(id) {
    return this.json(`${API}/campaigns/${id}/export`);
  }

  async file(path) {
    return (await this.request(`${API}/media/${path.split("/").map(encodeURIComponent).join("/")}`)).blob();
  }
}

/* ---------------------------------------------------------------------------------------------- */
/* Import                                                                                          */
/* ---------------------------------------------------------------------------------------------- */

class MazeImporter {
  /**
   * @param {MazeClient} client
   * @param {number} campaignId
   * @param {object} options { npc, faction, place, maps, actors, overwrite, fullMaps, scenario, related }
   * @param {(line: string) => void} log
   */
  constructor(client, campaignId, options, log) {
    this.client = client;
    this.cid = campaignId;
    this.options = options;
    this.log = log;
    this.report = { created: 0, updated: 0, unchanged: 0, skipped: [], removed: 0, duplicates: [], errors: [] };
    this.folders = new Map();       // type|clé > Folder
    this.journals = new Map();      // "npc:12" > JournalEntry
    this.scenesByPlace = new Map(); // id du lieu > [Scene]
    this.paths = new Map();         // chemin dans Maze > chemin dans Foundry
    this.stale = { JournalEntry: [], Scene: [], Actor: [] };
  }

  async run() {
    this.log("Lecture de la campagne dans Maze…");
    this.data = await this.client.exportCampaign(this.cid);
    const d = this.data;
    this.instance = d.instance;
    this.log(`${d.campaign.name} : ${d.npcs.length} PNJ, ${d.factions.length} factions, ${d.places.length} lieux, ${d.maps.length} cartes.`);
    // Scénario choisi : seulement ses fiches (et, au choix, leurs voisines). Les autres déjà importées restent
    // telles quelles et servent toujours de cibles aux liens ; elles ne passent pas dans « Retirés de Maze ».
    this.selection = null;
    if (this.options.scenario) {
      const sc = (d.scenarios ?? []).find((s) => s.id === this.options.scenario);
      if (!sc?.cast) throw new Error("Scénario introuvable dans Maze (supprimé ?) : reconnectez-vous pour rafraîchir la liste.");
      this.selection = Object.fromEntries(KINDS.map((k) => [k, new Set([
        ...(sc.cast[k] ?? []), ...(this.options.related ? sc.related?.[k] ?? [] : [])])]));
      this.scenarioTitle = sc.title;
    }
    const sel = this.selection;
    const picked = (kind, list) => (sel ? list.filter((e) => sel[kind].has(e.id)) : list);
    this.entities = Object.fromEntries(KINDS.map((k) => [k, this.options[k] ? picked(k, d[KIND_DATA[k]]) : []]));
    this.actorNpcs = picked("npc", d.npcs);
    // Cartes d'un scénario : celles de ses lieux et celles où l'un de ses lieux est épinglé.
    this.syncMaps = sel ? d.maps.filter((m) => (m.place != null && sel.place.has(m.place))
      || m.pins.some((pin) => sel.place.has(pin.place))) : d.maps;
    if (sel) {
      this.log(`Scénario « ${this.scenarioTitle} » : ${sel.npc.size} PNJ, ${sel.faction.size} factions, ${sel.place.size} lieux, ${this.syncMaps.length} carte(s).`);
    }
    await this.uploadFiles();
    await this.ensureJournals();
    if (this.options.maps) await this.syncScenes();
    else this.knownScenes();
    await this.fillJournals();
    if (this.options.actors) await this.syncActors();
    await this.retireMissing();
    return this.report;
  }

  /* --- Documents de cette campagne -------------------------------------------------------------- */

  ownFlags(extra = {}) {
    return { [MODULE_ID]: { instance: this.instance, campaign: this.cid, ...extra } };
  }

  /** Documents importés de cette campagne (y compris ceux rangés dans « Retirés de Maze »). */
  ours(collection, kind) {
    return collection.filter((doc) => {
      const f = flagOf(doc);
      return f?.instance === this.instance && f.campaign === this.cid && (kind ? f.kind === kind : !!f.kind);
    });
  }

  /**
   * Associe les documents existants aux fiches de Maze (`items` : {id, created}). Un document dont la date de
   * création ne correspond plus (identifiant réutilisé dans Maze après une suppression) est périmé ; plusieurs
   * documents pour une même fiche (copie faite dans Foundry) : le plus ancien est gardé, les autres signalés.
   */
  match(docs, items, label) {
    const created = new Map(items.map((e) => [e.id, e.created]));
    const groups = new Map();
    for (const doc of docs) {
      const f = flagOf(doc);
      if (!created.has(f.id)) {
        this.staleDoc(doc);
        continue;
      }
      if (f.created && f.created !== created.get(f.id)) {
        this.staleDoc(doc);
        continue;
      }
      if (!groups.has(f.id)) groups.set(f.id, []);
      groups.get(f.id).push(doc);
    }
    const out = new Map();
    for (const [id, list] of groups) {
      list.sort((a, b) => createdTime(a) - createdTime(b));
      out.set(id, list[0]);
      for (const copy of list.slice(1)) this.report.duplicates.push(`${label} : ${copy.name}`);
    }
    return out;
  }

  staleDoc(doc) {
    if (!flagOf(doc).removed) this.stale[doc.documentName]?.push(doc);
  }

  /** Changements de nom et de dossier, et retour d'une fiche « retirée » réapparue dans Maze. */
  placement(doc, name, folder) {
    const update = {};
    if (doc.name !== name) update.name = name;
    if ((doc.folder?.id ?? null) !== folder.id) update.folder = folder.id;
    if (flagOf(doc).removed) update[`flags.${MODULE_ID}.removed`] = false;
    return update;
  }

  /* --- Dossiers ------------------------------------------------------------------------------ */

  async folder(type, key, name, parent = null) {
    const id = `${type}|${key}`;
    if (this.folders.has(id)) return this.folders.get(id);
    let folder = game.folders.find((f) => f.type === type && flagOf(f)?.instance === this.instance
      && flagOf(f)?.campaign === this.cid && flagOf(f)?.key === key);
    const parentId = parent?.id ?? null;
    if (!folder) {
      folder = await Folder.implementation.create({
        name, type, folder: parentId, sorting: "a", flags: this.ownFlags({ key })
      });
    } else if (folder.name !== name || (folder.folder?.id ?? null) !== parentId) {
      await folder.update({ name, folder: parentId });
    }
    this.folders.set(id, folder);
    return folder;
  }

  rootName(label) {
    return `${this.data.campaign.name} - ${label}`;
  }

  /** Dossier d'un journal (ou d'un acteur) selon sa nature et sa place dans Maze. */
  async folderFor(type, kind, entity) {
    if (kind === "faction") return this.folder(type, "faction-root", this.rootName("Factions"));
    if (kind === "npc") {
      const root = await this.folder(type, "npc-root", this.rootName("PNJ"));
      const group = entity.group ? this.data.factions.find((f) => f.id === entity.group) : null;
      if (!group) return this.folder(type, "npc-group:none", "Sans faction", root);
      return this.folder(type, `npc-group:${group.id}`, group.name, root);
    }
    // Lieux : un dossier par lieu qui a des sous-lieux, tant que la profondeur le permet.
    return entity.has_children ? this.placeFolder(entity) : this.placeFolder(this.placeById(entity.parent));
  }

  placeById(id) {
    return id == null ? null : this.data.places.find((p) => p.id === id) ?? null;
  }

  async placeFolder(place) {
    const root = await this.folder("JournalEntry", "place-root", this.rootName("Lieux"));
    if (!place) return root;
    const parent = await this.placeFolder(this.placeById(place.parent));
    const childDepth = (parent.depth ?? (parent.ancestors?.length ?? 0) + 1) + 1;
    if (childDepth > maxDepth()) return parent;
    return this.folder("JournalEntry", `place:${place.id}`, place.name, parent);
  }

  /* --- Fichiers -------------------------------------------------------------------------------- */

  mapFile(map) {
    return this.options.fullMaps || !map.preview ? map.file : map.preview;
  }

  async uploadFiles() {
    const d = this.data;
    const wanted = new Set();
    for (const kind of KINDS) {
      for (const e of this.entities[kind]) for (const img of e.images) if (d.files[img.id]) wanted.add(d.files[img.id].path);
    }
    if (this.options.actors) for (const n of this.actorNpcs) if (n.cover && d.files[n.cover]) wanted.add(d.files[n.cover].path);
    if (this.options.maps) {
      for (const m of this.syncMaps) wanted.add(this.mapFile(m));
      for (const k of d.place_kinds) if (k.icon) wanted.add(k.icon);
    }
    if (!wanted.size) return;

    const FP = filePicker();
    const dir = `worlds/${game.world.id}/maze/${this.instance}-${this.cid}`;
    for (const part of [`worlds/${game.world.id}/maze`, dir]) {
      try { await FP.createDirectory("data", part, {}); } catch (err) { /* existe déjà */ }
    }
    let existing = new Map();
    try {
      const listing = await FP.browse("data", dir);
      existing = new Map(listing.files.map((f) => [decodeURIComponent(f.split("/").pop()), f]));
    } catch (err) { /* dossier vide */ }

    const todo = [];
    for (const path of wanted) {
      const name = path.split("/").pop();
      if (existing.has(name)) this.paths.set(path, existing.get(name));
      else todo.push(path);
    }
    if (!todo.length) return;
    this.log(`Téléchargement de ${todo.length} image(s)…`);
    let done = 0;
    await pool(todo, 4, async (path) => {
      const name = path.split("/").pop();
      try {
        const blob = await this.client.file(path);
        const file = new File([blob], name, { type: blob.type || (name.endsWith(".jpg") ? "image/jpeg" : "image/webp") });
        // FilePicker.upload ne lève pas d'erreur quand Foundry refuse le fichier : il renvoie false ou un
        // objet sans chemin.
        const result = await FP.upload("data", dir, file, {}, { notify: false });
        if (!result?.path) throw new Error(result?.message || "dépôt refusé par Foundry");
        this.paths.set(path, result.path);
      } catch (err) {
        this.report.errors.push(`Image ${name} : ${err.message ?? err}`);
      }
      done += 1;
      if (done % 20 === 0) this.log(`… ${done} / ${todo.length}`);
    });
  }

  imagePath(imageId) {
    const file = imageId != null ? this.data.files[imageId] : null;
    return file ? this.paths.get(file.path) ?? null : null;
  }

  /* --- Journaux -------------------------------------------------------------------------------- */

  async ensureJournals() {
    const create = [];
    for (const kind of KINDS) {
      if (!this.options[kind]) {
        // Fiches non importées cette fois : leurs journaux existants servent encore de cibles aux liens.
        for (const doc of this.ours(game.journal, kind)) {
          if (!flagOf(doc).removed) this.journals.set(`${kind}:${flagOf(doc).id}`, doc);
        }
        continue;
      }
      // Rapprochement sur toutes les fiches de Maze : une fiche hors du scénario n'est pas « disparue ».
      const matched = this.match(this.ours(game.journal, kind), this.data[KIND_DATA[kind]], KIND_LABEL[kind]);
      const chosen = new Set(this.entities[kind].map((e) => e.id));
      for (const [id, doc] of matched) {
        if (!chosen.has(id) && !flagOf(doc).removed) this.journals.set(`${kind}:${id}`, doc);
      }
      for (const e of this.entities[kind]) {
        const folder = await this.folderFor("JournalEntry", kind, e);
        const doc = matched.get(e.id);
        if (!doc) {
          create.push({ name: e.name, folder: folder.id, flags: this.ownFlags({ kind, id: e.id, created: e.created }) });
          continue;
        }
        this.journals.set(`${kind}:${e.id}`, doc);
        const update = this.placement(doc, e.name, folder);
        if (!flagOf(doc).created) update[`flags.${MODULE_ID}.created`] = e.created;
        if (Object.keys(update).length) await doc.update(update);
      }
    }
    this.fresh = new Set();
    if (create.length) {
      this.log(`Création de ${create.length} journal(aux)…`);
      const created = await inBatches(create, 100, (batch) => JournalEntry.implementation.createDocuments(batch));
      for (const doc of created) {
        this.journals.set(`${flagOf(doc).kind}:${flagOf(doc).id}`, doc);
        this.fresh.add(doc.id);
      }
      this.report.created += created.length;
    }
  }

  /** Remplace les marqueurs du HTML de Maze : liens vers les journaux importés, chemins des images. */
  render(html) {
    return (html ?? "")
      .replace(/<a data-maze="(npc|faction|place):(\d+)">([\s\S]*?)<\/a>/g, (all, kind, id, label) => {
        const doc = this.journals.get(`${kind}:${id}`);
        return doc ? uuidLink(doc, decodeHtml(label.replace(/<[^>]*>/g, ""))) : label;
      })
      .replace(/<p><img data-maze-image="(\d+)" alt="([^"]*)"><\/p>/g, (all, id, alt) => {
        const src = this.imagePath(Number(id));
        return src ? `<p><img src="${escapeHtml(src)}" alt="${alt}"></p>` : "";
      });
  }

  sheetHtml(kind, entity) {
    let html = this.render(entity.html);
    if (kind === "place") {
      const scenes = this.scenesByPlace.get(entity.id) ?? [];
      if (scenes.length) html += "<h2>Cartes</h2><ul>" + scenes.map((s) => `<li><p>${uuidLink(s, s.name)}</p></li>`).join("") + "</ul>";
    }
    return html;
  }

  async fillJournals() {
    const total = KINDS.reduce((n, k) => n + this.entities[k].length, 0);
    if (!total) return;
    this.log(`Mise à jour du contenu de ${total} journal(aux)…`);
    let done = 0;
    for (const kind of KINDS) {
      for (const e of this.entities[kind]) {
        const doc = this.journals.get(`${kind}:${e.id}`);
        if (!doc) continue;
        try {
          const result = await this.fillJournal(doc, kind, e);
          if (result.updated && !this.fresh.has(doc.id)) this.report.updated += 1;
          else if (!result.updated && !result.skipped.length) this.report.unchanged += 1;
          for (const page of result.skipped) this.report.skipped.push(`${KIND_LABEL[kind]} : ${e.name} (${page})`);
        } catch (err) {
          this.report.errors.push(`${e.name} : ${err.message ?? err}`);
        }
        done += 1;
        if (done % 50 === 0) this.log(`… ${done} / ${total}`);
      }
    }
  }

  async fillJournal(doc, kind, entity) {
    const result = { updated: false, skipped: [] };
    const ownership = CONST.DOCUMENT_OWNERSHIP_LEVELS;
    const texts = [
      { role: "sheet", name: "Fiche", html: this.sheetHtml(kind, entity), sort: 0, title: false },
      { role: "secrets", name: "Secrets (MJ)", html: this.render(entity.secret_html), sort: 50, title: true,
        // Page réservée au MJ, même si le journal est partagé avec les joueurs.
        ownership: { default: ownership?.NONE ?? 0 } }
    ];
    const written = [];
    for (const t of texts) {
      const page = doc.pages.find((p) => flagOf(p)?.role === t.role) ?? null;
      const source = hashText(t.html);
      if (!page) {
        if (!t.html) continue;
        const [created] = await doc.createEmbeddedDocuments("JournalEntryPage", [{
          name: t.name, type: "text", sort: t.sort, title: { show: t.title, level: 1 },
          text: { content: t.html, format: CONST.JOURNAL_ENTRY_PAGE_FORMATS?.HTML ?? 1 },
          ...(t.ownership ? { ownership: t.ownership } : {}),
          flags: { [MODULE_ID]: { role: t.role, source } }
        }]);
        written.push(created);
        continue;
      }
      const f = flagOf(page);
      const current = page.text?.content ?? "";
      const modified = !!f.content && contentHash(current) !== f.content;
      if (f.source === source && !modified) continue;
      if (modified && !this.options.overwrite) {
        if (f.source !== source) result.skipped.push(t.name);
        continue;
      }
      if (!t.html) {
        await doc.deleteEmbeddedDocuments("JournalEntryPage", [page.id]);
        result.updated = true;
        continue;
      }
      await page.update({ "text.content": keepRevealed(t.html, revealedIds(current)), [`flags.${MODULE_ID}.source`]: source });
      written.push(page);
    }
    // Empreinte du texte tel que Foundry l'a enregistré : repère les modifications faites ensuite dans Foundry.
    if (written.length) {
      result.updated = true;
      await doc.updateEmbeddedDocuments("JournalEntryPage", written.map((p) => ({
        _id: p.id, [`flags.${MODULE_ID}.content`]: contentHash(doc.pages.get(p.id)?.text?.content ?? "")
      })));
    }
    if (await this.syncImagePages(doc, entity)) result.updated = true;
    return result;
  }

  /** Une page par image de la galerie (le MJ peut la montrer aux joueurs). */
  async syncImagePages(doc, entity) {
    const gallery = entity.images.map((img, n) => ({ img, src: this.imagePath(img.id), n })).filter((x) => x.src);
    const wanted = new Map(gallery.map((x) => [`image:${x.img.id}`, x]));
    const pages = doc.pages.filter((p) => String(flagOf(p)?.role ?? "").startsWith("image:"));
    const same = pages.length === wanted.size && pages.every((p) => {
      const want = wanted.get(flagOf(p).role);
      return want && p.src === want.src && p.name === this.imageTitle(entity, want);
    });
    if (same) return false;
    const remove = pages.filter((p) => !wanted.has(flagOf(p).role)).map((p) => p.id);
    if (remove.length) await doc.deleteEmbeddedDocuments("JournalEntryPage", remove);
    const create = [], update = [];
    for (const [role, x] of wanted) {
      const existing = pages.find((p) => flagOf(p).role === role);
      const data = { name: this.imageTitle(entity, x), src: x.src, sort: 100 + x.n, image: { caption: x.img.caption || "" } };
      if (existing) update.push({ _id: existing.id, ...data });
      else create.push({ ...data, type: "image", title: { show: true, level: 1 }, flags: { [MODULE_ID]: { role } } });
    }
    if (update.length) await doc.updateEmbeddedDocuments("JournalEntryPage", update);
    if (create.length) await doc.createEmbeddedDocuments("JournalEntryPage", create);
    return true;
  }

  imageTitle(entity, x) {
    return x.img.caption?.trim() || (x.n === 0 ? entity.name : `${entity.name} (${x.n + 1})`);
  }

  /* --- Scènes ---------------------------------------------------------------------------------- */

  /** Sans import des cartes : les scènes déjà importées restent citées dans la fiche de leur lieu. */
  knownScenes() {
    const maps = new Map(this.data.maps.map((m) => [m.id, m]));
    for (const scene of this.ours(game.scenes, "map")) {
      const map = maps.get(flagOf(scene).id);
      if (!map || flagOf(scene).removed || (flagOf(scene).created && flagOf(scene).created !== map.created)) continue;
      this.addSceneOfPlace(map, scene);
    }
  }

  addSceneOfPlace(map, scene) {
    if (map.place == null) return;
    if (!this.scenesByPlace.has(map.place)) this.scenesByPlace.set(map.place, []);
    this.scenesByPlace.get(map.place).push(scene);
  }

  async syncScenes() {
    const d = this.data;
    const existing = this.match(this.ours(game.scenes, "map"), d.maps, "Carte");
    const chosen = new Set(this.syncMaps.map((m) => m.id));
    for (const map of d.maps) {
      const scene = existing.get(map.id);
      if (!chosen.has(map.id) && scene && !flagOf(scene).removed) this.addSceneOfPlace(map, scene);
    }
    if (!this.syncMaps.length) return;
    this.log(`Cartes : ${this.syncMaps.length} scène(s)…`);
    const folder = await this.folder("Scene", "scene-root", this.rootName("Cartes"));
    const icons = new Map(d.place_kinds.map((k) => [k.key, k.icon ? this.paths.get(k.icon) : null]));
    const places = new Map(d.places.map((p) => [p.id, p]));
    const v14 = game.release.generation >= 14;
    for (const map of this.syncMaps) {
      let scene = existing.get(map.id);
      try {
        const src = this.paths.get(this.mapFile(map));
        if (!src) throw new Error("fond de carte non téléchargé");
        const base = { name: map.name, folder: folder.id, width: map.width, height: map.height, padding: 0 };
        if (!scene) {
          // Foundry 14 : le fond appartient au niveau de la scène (posé juste après).
          scene = await Scene.implementation.create({
            ...base, ...(v14 ? {} : { background: { src } }), navigation: false,
            grid: { type: CONST.GRID_TYPES.GRIDLESS },
            flags: this.ownFlags({ kind: "map", id: map.id, created: map.created })
          });
          this.report.created += 1;
        } else {
          const f = flagOf(scene);
          const update = this.placement(scene, map.name, folder);
          if (f.hash !== map.hash || f.src !== src) Object.assign(update, { width: map.width, height: map.height },
            v14 ? {} : { background: { src } });
          if (!f.created) update[`flags.${MODULE_ID}.created`] = map.created;
          if (Object.keys(update).length) {
            await scene.update(update);
            this.report.updated += 1;
          } else {
            this.report.unchanged += 1;
          }
        }
        const level = v14 ? await this.sceneLevel(scene, map, src) : null;
        await this.syncNotes(scene, map, level, icons, places);
        const f = flagOf(scene);
        if (f.hash !== map.hash || f.src !== src) {
          await scene.update({ [`flags.${MODULE_ID}.hash`]: map.hash, [`flags.${MODULE_ID}.src`]: src });
        }
        this.addSceneOfPlace(map, scene);
      } catch (err) {
        this.report.errors.push(`Carte ${map.name} : ${err.message ?? err}`);
      }
    }
  }

  /** Foundry 14 (Scene Levels) : fond posé sur le niveau initial de la scène, créé s'il n'existe pas. */
  async sceneLevel(scene, map, src) {
    let level = scene.initialLevel ?? scene.firstLevel ?? scene.levels?.contents?.[0] ?? null;
    if (!level) {
      [level] = await scene.createEmbeddedDocuments("Level", [{ name: map.name, background: { src } }]);
    } else if (level.background?.src !== src) {
      await scene.updateEmbeddedDocuments("Level", [{ _id: level.id, "background.src": src }]);
    }
    return level ?? null;
  }

  async syncNotes(scene, map, level, icons, places) {
    const notes = scene.notes.filter((n) => flagOf(n)?.place != null);
    const byPlace = new Map(notes.map((n) => [flagOf(n).place, n]));
    const iconSize = Math.max(32, Math.round(Math.min(map.width, map.height) / 50));
    const create = [], update = [];
    for (const pin of map.pins) {
      const place = places.get(pin.place);
      const journal = this.journals.get(`place:${pin.place}`);
      const data = {
        x: Math.round(pin.x * map.width), y: Math.round(pin.y * map.height),
        entryId: journal?.id ?? null,
        text: place?.name ?? "",
        texture: { src: icons.get(place?.kind) || DEFAULT_NOTE_ICON },
        iconSize, fontSize: Math.max(24, Math.round(iconSize * 0.6))
      };
      const note = byPlace.get(pin.place);
      if (note) {
        byPlace.delete(pin.place);
        const same = note.x === data.x && note.y === data.y && (note.entryId ?? null) === data.entryId
          && note.text === data.text && note.texture?.src === data.texture.src && note.iconSize === data.iconSize;
        if (!same) update.push({ _id: note.id, ...data });
      } else {
        if (level) data.levels = [level.id];
        create.push({ ...data, flags: { [MODULE_ID]: { place: pin.place } } });
      }
    }
    if (byPlace.size) await scene.deleteEmbeddedDocuments("Note", [...byPlace.values()].map((n) => n.id));
    if (update.length) await scene.updateEmbeddedDocuments("Note", update);
    if (create.length) await scene.createEmbeddedDocuments("Note", create);
  }

  /* --- Acteurs --------------------------------------------------------------------------------- */

  async syncActors() {
    const system = ACTOR_SYSTEMS[game.system.id];
    if (!system) {
      this.report.errors.push(`Acteurs : système « ${game.system.id} » non pris en charge (journaux seulement).`);
      return;
    }
    const npcs = this.actorNpcs;
    if (!npcs.length) return;
    this.log(`Acteurs : ${npcs.length} PNJ…`);
    const existing = this.match(this.ours(game.actors, "npc"), this.data.npcs, "Acteur");
    const written = [];
    for (const npc of npcs) {
      try {
        const folder = await this.folderFor("Actor", "npc", npc);
        const journal = this.journals.get(`npc:${npc.id}`);
        const bio = (journal ? `<p>${uuidLink(journal, `Fiche de ${npc.name} (Maze)`)}</p>` : "") + this.render(npc.actor_bio);
        const bioHash = hashText(bio);
        const img = this.imagePath(npc.cover);
        const actor = existing.get(npc.id);
        if (!actor) {
          const data = {
            name: npc.name, type: system.type, folder: folder.id,
            flags: this.ownFlags({ kind: "npc", id: npc.id, created: npc.created, bio: bioHash, img })
          };
          if (img) {
            data.img = img;
            data.prototypeToken = { name: npc.name, texture: { src: img } };
          }
          foundry.utils.setProperty(data, system.bio, bio);
          if (system.faction) foundry.utils.setProperty(data, system.faction, npc.faction_names[0] ?? "");
          written.push(await Actor.implementation.create(data));
          this.report.created += 1;
          continue;
        }
        const f = flagOf(actor);
        const update = this.placement(actor, npc.name, folder);
        if (!f.created) update[`flags.${MODULE_ID}.created`] = npc.created;
        // Portrait : remplacé seulement s'il est toujours celui posé par l'import (ou celui par défaut).
        const untouched = f.img ? actor.img === f.img
          : !actor.img || actor.img === CONST.DEFAULT_TOKEN || actor.img.startsWith("icons/svg/mystery-man");
        if (img && f.img !== img && untouched) {
          update.img = img;
          update["prototypeToken.texture.src"] = img;
          update[`flags.${MODULE_ID}.img`] = img;
        }
        // Biographie : remplacée si elle a changé dans Maze et n'a pas été modifiée dans Foundry.
        const current = foundry.utils.getProperty(actor, system.bio) ?? "";
        const modified = !!f.bioStored && hashText(current) !== f.bioStored;
        if (bioHash !== f.bio) {
          if (!modified || this.options.overwrite) {
            update[system.bio] = bio;
            update[`flags.${MODULE_ID}.bio`] = bioHash;
          } else {
            this.report.skipped.push(`Acteur : ${npc.name} (biographie)`);
          }
        }
        if (Object.keys(update).length) {
          await actor.update(update);
          this.report.updated += 1;
          if (update[system.bio] !== undefined) written.push(actor);
        } else {
          this.report.unchanged += 1;
        }
      } catch (err) {
        this.report.errors.push(`Acteur ${npc.name} : ${err.message ?? err}`);
      }
    }
    // Empreinte de la biographie telle qu'enregistrée, pour les seuls acteurs écrits par cet import.
    await inBatches(written, 100, (batch) => Actor.implementation.updateDocuments(batch.map((a) => ({
      _id: a.id, [`flags.${MODULE_ID}.bioStored`]: hashText(foundry.utils.getProperty(a, system.bio) ?? "")
    }))));
  }

  /* --- Fiches supprimées dans Maze --------------------------------------------------------------- */

  async retireMissing() {
    for (const [type, docs] of Object.entries(this.stale)) {
      if (!docs.length) continue;
      const folder = await this.folder(type, "removed", "Retirés de Maze");
      for (const doc of docs) {
        await doc.update({ folder: folder.id, [`flags.${MODULE_ID}.removed`]: true });
        this.report.removed += 1;
      }
    }
  }
}

/* ---------------------------------------------------------------------------------------------- */
/* Fenêtre                                                                                         */
/* ---------------------------------------------------------------------------------------------- */

const { ApplicationV2 } = foundry.applications.api;

class MazeImportApp extends ApplicationV2 {
  static DEFAULT_OPTIONS = {
    id: APP_ID,
    tag: "div",
    classes: ["maze-sync"],
    window: { title: "Importer depuis Maze", icon: "fa-solid fa-dungeon", resizable: true },
    position: { width: 560, height: "auto" }
  };

  /** Ouvre la fenêtre, ou ramène au premier plan celle qui est déjà ouverte. */
  static open() {
    const app = foundry.applications.instances?.get(APP_ID) ?? new MazeImportApp();
    return app.render({ force: true });
  }

  constructor(options) {
    super(options);
    // « state » est réservé par ApplicationV2 (état de rendu, accesseur sans mutateur) : nom propre au module.
    this.view = { campaigns: null, busy: false, lines: [], report: null, error: "", related: false };
  }

  get client() {
    return new MazeClient(game.settings.get(MODULE_ID, "url"), game.settings.get(MODULE_ID, "token"));
  }

  async _renderHTML() {
    const s = this.view;
    const url = game.settings.get(MODULE_ID, "url");
    const hasToken = !!game.settings.get(MODULE_ID, "token");
    const actorSystem = ACTOR_SYSTEMS[game.system.id];
    const div = document.createElement("div");
    div.className = "maze-sync__body";
    // Campagne choisie par défaut : la dernière importée depuis cette adresse de Maze.
    const [lastUrl, lastId] = String(game.settings.get(MODULE_ID, "lastCampaign") || "").split("|");
    const last = lastUrl === url ? Number(lastId) : null;
    const campaigns = s.campaigns ?? [];
    const current = campaigns.find((c) => c.id === (s.campaignId ?? last)) ?? campaigns[0];
    const campaignOptions = campaigns.map((c) =>
      `<option value="${c.id}"${c === current ? " selected" : ""}>${escapeHtml(c.name)} (${c.npcs} PNJ, ${c.factions} factions, ${c.places} lieux, ${c.maps} cartes)</option>`).join("");
    const scenarioOptions = this.scenarioOptions(current);
    div.innerHTML = `
      <fieldset>
        <legend>Connexion à Maze</legend>
        <div class="form-group"><label>Adresse de Maze</label>
          <input type="url" name="url" value="${escapeHtml(url)}" placeholder="https://maze.example.org"></div>
        <div class="form-group"><label>Jeton</label>
          <input type="password" name="token" autocomplete="off" placeholder="${hasToken ? "enregistré (laisser vide pour le garder)" : "mzf_…"}"></div>
        <p class="hint">Jeton créé dans Maze, Administration > Foundry VTT. Il reste dans ce navigateur.</p>
        <button type="button" data-do="connect" ${s.busy ? "disabled" : ""}><i class="fa-solid fa-plug"></i> Se connecter</button>
      </fieldset>
      ${s.campaigns ? `
      <fieldset>
        <legend>Import</legend>
        ${s.campaigns.length ? `
        <div class="form-group"><label>Campagne</label><select name="campaign">${campaignOptions}</select></div>
        <div class="form-group"><label>Scénario</label><select name="scenario">${scenarioOptions}</select></div>
        <p class="hint">Un scénario : seulement les fiches qu'il cite (@), lui et ses pages, avec les ajouts et retraits de sa carte, et les cartes de ses lieux. Les autres fiches déjà importées ne sont pas touchées.</p>
        <div class="maze-sync__checks">
          <label class="maze-sync__related"><input type="checkbox" name="related" ${s.related ? "checked" : ""}> Avec les fiches voisines : factions des PNJ, lieux où ils se trouvent, présents des lieux cités</label>
          <label><input type="checkbox" name="npc" checked> Journaux des PNJ</label>
          <label><input type="checkbox" name="faction" checked> Journaux des factions</label>
          <label><input type="checkbox" name="place" checked> Journaux des lieux</label>
          <label><input type="checkbox" name="maps" checked> Cartes interactives en scènes</label>
          <label><input type="checkbox" name="actors" ${actorSystem ? "checked" : "disabled"}> Acteurs des PNJ${actorSystem ? "" : ` (système « ${escapeHtml(game.system.id)} » non pris en charge)`}</label>
          <label><input type="checkbox" name="fullMaps"> Fonds de carte en pleine définition (plus lourds)</label>
          <label><input type="checkbox" name="overwrite"> Remplacer aussi ce qui a été modifié dans Foundry</label>
        </div>
        <button type="button" data-do="import" ${s.busy ? "disabled" : ""}><i class="fa-solid fa-file-import"></i> Importer</button>`
        : `<p>Aucune campagne n'est ouverte à ce jeton.</p>`}
      </fieldset>` : ""}
      ${s.error ? `<p class="maze-sync__error">${escapeHtml(s.error)}</p>` : ""}
      ${s.lines.length ? `<pre class="maze-sync__log">${s.lines.map(escapeHtml).join("\n")}</pre>` : ""}
      ${s.report ? this.reportHtml(s.report) : ""}`;
    return div;
  }

  _replaceHTML(result, content) {
    content.replaceChildren(result);
  }

  /** Choix du scénario : toute la campagne, ou un de ses scénarios (le dernier importé est repris). */
  scenarioOptions(campaign) {
    const url = game.settings.get(MODULE_ID, "url");
    const [lastUrl, lastCid, lastSid] = String(game.settings.get(MODULE_ID, "lastScenario") || "").split("|");
    const wanted = this.view.scenarioId !== undefined && this.view.scenarioCampaign === campaign?.id ? this.view.scenarioId
      : lastUrl === url && Number(lastCid) === campaign?.id ? Number(lastSid) || null : null;
    const scenarios = campaign?.scenarios ?? [];
    return `<option value="">Toute la campagne</option>` + scenarios.map((sc) => {
      const status = SCENARIO_STATUS[sc.status] ? ` (${SCENARIO_STATUS[sc.status]})` : "";
      return `<option value="${sc.id}"${sc.id === wanted ? " selected" : ""}>${escapeHtml(sc.title || "Sans titre")}${status}</option>`;
    }).join("");
  }

  syncRelated() {
    const scenario = this.element.querySelector('[name="scenario"]');
    const related = this.element.querySelector('[name="related"]');
    if (!scenario || !related) return;
    related.disabled = !scenario.value;
    related.closest("label")?.classList.toggle("disabled", !scenario.value);
  }

  _onRender() {
    const campaign = this.element.querySelector('[name="campaign"]');
    const scenario = this.element.querySelector('[name="scenario"]');
    campaign?.addEventListener("change", () => {
      const c = (this.view.campaigns ?? []).find((x) => x.id === Number(campaign.value));
      this.view.campaignId = c?.id;
      scenario.innerHTML = this.scenarioOptions(c);
      this.syncRelated();
    });
    scenario?.addEventListener("change", () => {
      this.view.scenarioCampaign = Number(campaign.value);
      this.view.scenarioId = Number(scenario.value) || null;
      this.syncRelated();
    });
    this.element.querySelector('[name="related"]')?.addEventListener("change", (e) => { this.view.related = e.target.checked; });
    this.syncRelated();
    this.element.querySelector('[data-do="connect"]')?.addEventListener("click", () => this.connect());
    this.element.querySelector('[data-do="import"]')?.addEventListener("click", () => this.startImport());
  }

  reportHtml(r) {
    const list = (title, items) => items.length
      ? `<details><summary>${title} (${items.length})</summary><ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul></details>` : "";
    return `<div class="maze-sync__report">
      <p><strong>Terminé :</strong> ${r.created} créé(s), ${r.updated} mis à jour, ${r.unchanged} inchangé(s)${r.removed ? `, ${r.removed} retiré(s) de Maze` : ""}.</p>
      ${list("Modifiés dans Foundry, laissés tels quels", r.skipped)}
      ${list("Copies faites dans Foundry, ignorées", r.duplicates)}
      ${list("Erreurs", r.errors)}
    </div>`;
  }

  async connect() {
    const url = this.element.querySelector('[name="url"]').value.trim();
    const token = this.element.querySelector('[name="token"]').value.trim();
    await game.settings.set(MODULE_ID, "url", url);
    if (token) await game.settings.set(MODULE_ID, "token", token);
    this.view.error = "";
    this.view.busy = true;
    await this.render();
    try {
      this.view.campaigns = await this.client.campaigns();
    } catch (err) {
      this.view.campaigns = null;
      this.view.error = err.message ?? String(err);
    }
    this.view.busy = false;
    await this.render();
  }

  async startImport() {
    const el = this.element;
    const checked = (name) => !!el.querySelector(`[name="${name}"]`)?.checked;
    const campaignId = Number(el.querySelector('[name="campaign"]').value);
    const scenarioId = Number(el.querySelector('[name="scenario"]')?.value) || null;
    const url = game.settings.get(MODULE_ID, "url");
    await game.settings.set(MODULE_ID, "lastCampaign", `${url}|${campaignId}`);
    await game.settings.set(MODULE_ID, "lastScenario", `${url}|${campaignId}|${scenarioId ?? ""}`);
    Object.assign(this.view, { campaignId, scenarioCampaign: campaignId, scenarioId });
    const options = Object.fromEntries(["npc", "faction", "place", "maps", "actors", "fullMaps", "overwrite"]
      .map((k) => [k, checked(k)]));
    options.scenario = scenarioId;
    options.related = !!scenarioId && checked("related");
    Object.assign(this.view, { busy: true, lines: [], report: null, error: "" });
    await this.render();
    const log = (line) => {
      this.view.lines.push(line);
      const pre = this.element?.querySelector(".maze-sync__log");
      if (pre) pre.textContent = this.view.lines.join("\n");
      else this.render();
    };
    try {
      this.view.report = await new MazeImporter(this.client, campaignId, options, log).run();
      ui.notifications.info("Import depuis Maze terminé.");
    } catch (err) {
      console.error(`${MODULE_ID} |`, err);
      this.view.error = err.message ?? String(err);
    }
    this.view.busy = false;
    await this.render();
  }
}

/* ---------------------------------------------------------------------------------------------- */
/* Initialisation                                                                                  */
/* ---------------------------------------------------------------------------------------------- */

Hooks.once("init", () => {
  // Portée client : adresse et jeton restent dans le navigateur du MJ (les réglages du monde sont lisibles
  // par les joueurs).
  game.settings.register(MODULE_ID, "url", { scope: "client", config: false, type: String, default: "" });
  game.settings.register(MODULE_ID, "token", { scope: "client", config: false, type: String, default: "" });
  // Dernière campagne importée : « adresse de Maze|identifiant ».
  game.settings.register(MODULE_ID, "lastCampaign", { scope: "client", config: false, type: String, default: "" });
  // Dernier scénario importé : « adresse|campagne|scénario » (scénario vide : toute la campagne).
  game.settings.register(MODULE_ID, "lastScenario", { scope: "client", config: false, type: String, default: "" });
  game.settings.registerMenu(MODULE_ID, "import", {
    name: "Maze",
    label: "Importer depuis Maze",
    hint: "PNJ, factions, lieux et cartes d'une campagne de Maze en journaux, scènes et acteurs.",
    icon: "fa-solid fa-dungeon",
    type: MazeImportApp,
    restricted: true
  });
});

Hooks.once("ready", () => {
  const module = game.modules.get(MODULE_ID);
  if (module) module.api = { open: () => MazeImportApp.open(), MazeClient, MazeImporter };
});

// Bouton « Maze » en haut de l'onglet Journaux (MJ seulement).
Hooks.on("renderJournalDirectory", (app, html) => {
  try {
    if (!game.user.isGM) return;
    const root = html instanceof HTMLElement ? html : html?.[0];
    // Barre de boutons du haut de l'onglet (v13 : .header-actions) ; à défaut, en-tête du répertoire.
    const actions = root?.querySelector(".header-actions, .directory-header .action-buttons")
      ?? root?.querySelector(".directory-header, header");
    if (!actions || root.querySelector(".maze-sync-open")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "maze-sync-open";
    button.innerHTML = '<i class="fa-solid fa-dungeon"></i> Maze';
    button.addEventListener("click", () => MazeImportApp.open());
    actions.append(button);
  } catch (err) {
    console.warn(`${MODULE_ID} | bouton du répertoire des journaux`, err);
  }
});
