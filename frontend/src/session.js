import { api } from './api.js';
import { STALE_EVENT } from './editor/schemaVersion.js';
import { t } from './i18n/core.js';

/*
 * Ce qui ne doit jamais se perdre en silence (lot 3 de l'audit) :
 *   - saisies en cours des formulaires PNJ, factions et lieux (M3) : confirmation avant de les quitter, et
 *     avertissement du navigateur à la fermeture de l'onglet ;
 *   - modifications d'une fiche rédigée que le temps réel doit abandonner (M4, H3) : contenu remplacé pendant une
 *     coupure, modification trop grosse, fiche fermée par le serveur, autre version de Maze. Le HTML local est
 *     gardé comme version nommée de la fiche (à défaut dans ce navigateur) et un avis reste affiché jusqu'à ce que
 *     l'utilisateur le ferme ;
 *   - autre version du schéma de l'éditeur sur le serveur (H3) : la page est rechargée, sauf saisie en cours.
 */

const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn());
/** Abonnement aux changements (saisies, avis, version) ; renvoie la fonction de désabonnement. */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// --- Saisies en cours (M3) ----------------------------------------------------------------------

const unsaved = new Map();  // clé du formulaire -> nom de la fiche

export function setUnsaved(key, dirty, label = '') {
  const had = unsaved.has(key);
  if (dirty) unsaved.set(key, label);
  else unsaved.delete(key);
  if (had !== dirty) notify();
}

export const hasUnsaved = () => unsaved.size > 0;

/** Avant de quitter une saisie en cours (autre fiche, fond de la carte, navigation) : confirmation (useConfirm).
 * Renvoie true si l'on peut continuer ; les saisies abandonnées sont oubliées. */
export async function confirmDiscard(confirm) {
  if (!unsaved.size) return true;
  const names = [...unsaved.values()].filter(Boolean);
  const ok = await confirm({
    title: t('Abandonner la saisie en cours ?'),
    message: names.length
      ? t('Les modifications de {names} ne sont pas enregistrées. Elles seront perdues.',
        { names: names.map((name) => t('« {title} »', { title: name })).join(', ') })
      : t('Des modifications ne sont pas enregistrées. Elles seront perdues.'),
    confirmLabel: t('Abandonner'),
  });
  if (ok) {
    unsaved.clear();
    notify();
  }
  return ok;
}

window.addEventListener('beforeunload', (e) => {
  if (unsaved.size) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// --- Modifications de fiches rédigées abandonnées par le temps réel (M4, H3) --------------------
//
// Chaque texte gardé porte le compte et l'instance (poste partagé, contre-revue) : son avis n'est montré qu'à ce
// compte une fois connecté, jamais sur l'écran de connexion ni à un autre compte ; à la reconnexion de ce compte, il
// est de nouveau envoyé comme version nommée, puis retiré du navigateur ; il est effacé au-delà de KEEP_DAYS jours
// (date annoncée dans l'avis). Plusieurs onglets partagent la liste : elle est relue avant chaque modification et
// l'événement storage la tient à jour ; une fois écrite dans un stockage, elle est retirée de l'autre.

const LOST_KEY = 'maze:lost-edits';
export const KEEP_DAYS = 7;
const DAY = 24 * 3600 * 1000;
const STORES = [() => window.localStorage, () => window.sessionStorage];
let account = null;      // { id, username, instance } du compte connecté ; null : écran de connexion
let memoryOnly = [];     // textes qu'aucun stockage n'a pu garder : cette page seulement
let recovered = [];      // textes envoyés à la reconnexion : avis de cette page, plus rien dans le navigateur
const sending = new Set();
let pending = 0;
let waiters = [];

const expired = (e, now = Date.now()) => !(now - Date.parse(e.at) < KEEP_DAYS * DAY);
export const purgeDate = (e) => new Date(Date.parse(e.at) + KEEP_DAYS * DAY);

function readStored() {
  const byId = new Map();
  for (const store of STORES) {
    try {
      const raw = store().getItem(LOST_KEY);
      for (const e of raw ? JSON.parse(raw) : []) if (e?.id && !byId.has(e.id)) byId.set(e.id, e);
    } catch { /* stockage indisponible ou illisible */ }
  }
  return [...byId.values()];
}

function writeStored(list) {
  const raw = JSON.stringify(list);
  for (let i = 0; i < STORES.length; i += 1) {
    try {
      if (list.length) STORES[i]().setItem(LOST_KEY, raw);
      else STORES[i]().removeItem(LOST_KEY);
    } catch {
      continue;  // plein ou indisponible : stockage suivant
    }
    STORES.forEach((other, j) => { if (j !== i) { try { other().removeItem(LOST_KEY); } catch { /* indisponible */ } } });
    return true;
  }
  return false;
}

/** Modifie la liste gardée, relue juste avant (un autre onglet a pu la changer) ; textes trop anciens effacés.
 * Renvoie false si aucun stockage n'a pu l'écrire. */
function mutate(fn) {
  const stored = fn(readStored()).filter((e) => !expired(e));
  const ok = writeStored(stored);
  notify();
  return ok;
}

const mine = (e) => Boolean(account && e.user && e.user.id === account.id && e.user.username === account.username
  && e.instance === account.instance);

/** Avis du compte connecté (aucun sur l'écran de connexion). Un envoi interrompu (autre onglet fermé, rechargement)
 * est présenté comme gardé dans le navigateur. */
export function lostEdits() {
  if (!account) return [];
  const now = Date.now();
  return [...readStored(), ...memoryOnly, ...recovered].filter((e) => mine(e) && !expired(e, now)).map((e) => (
    e.status === 'saving' && !sending.has(e.id) && now - Date.parse(e.at) > 30000 ? { ...e, status: 'local' } : e));
}

/** Textes du compte connecté qui ne sont que dans ce navigateur (avertissement à la déconnexion). */
export const localEdits = () => lostEdits().filter((e) => e.status === 'local' && e.html);

function update(id, patch) {
  if (memoryOnly.some((e) => e.id === id)) {
    memoryOnly = memoryOnly.map((e) => (e.id === id ? { ...e, ...patch } : e));
    notify();
    return;
  }
  mutate((list) => list.map((e) => (e.id === id ? { ...e, ...patch } : e)));
}

function remove(ids) {
  const drop = new Set(ids);
  memoryOnly = memoryOnly.filter((e) => !drop.has(e.id));
  recovered = recovered.filter((e) => !drop.has(e.id));
  mutate((list) => list.filter((e) => !drop.has(e.id)));
}

export const dismissLost = (id) => remove([id]);
export const discardLocal = () => remove(localEdits().map((e) => e.id));

window.addEventListener('storage', (e) => { if (e.key === LOST_KEY || e.key === null) notify(); });

const withTimeout = (promise, ms) => Promise.race([
  promise,
  new Promise((_, reject) => { setTimeout(() => reject(new Error(t('le serveur ne répond pas'))), ms); }),
]);

/** Version nommée envoyée sans en-tête de version de schéma : un instantané de l'historique, que le serveur
 * accepte même d'une ancienne interface sans rien changer au document partagé. */
async function send(entry) {
  sending.add(entry.id);
  pending += 1;
  try {
    await withTimeout(api.createVersion(entry.docId, { label: entry.label, content: entry.html },
      { schemaHeader: false }), 10000);
    return null;
  } catch (err) {
    return err.message || String(err);
  } finally {
    sending.delete(entry.id);
    pending -= 1;
    if (!pending) waiters.splice(0).forEach((resolve) => resolve());
  }
}

/**
 * Garde le HTML local d'une fiche rédigée dont le temps réel abandonne des modifications non confirmées :
 * d'abord dans ce navigateur, puis comme version nommée de la fiche (« Modifications hors ligne de <nom> »).
 */
export async function rescueEdits({ docId, campaignId, created, title, html, reason, author }) {
  // Nom de la version, dans la langue de celui qui écrivait (gardé tel quel dans l'historique de la fiche).
  const label = t('Modifications hors ligne de {name}', { name: author || t('quelqu’un') }).slice(0, 120);
  const entry = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, docId, campaignId, created, title, html,
    reason, label, at: new Date().toISOString(), status: 'saving',
    user: account ? { id: account.id, username: account.username } : null, instance: account?.instance ?? null };
  if (!mutate((list) => [...list, entry])) memoryOnly = [...memoryOnly, { ...entry, memoryOnly: true }];
  notify();
  const error = await send(entry);
  // Gardée côté serveur : le texte n'a plus besoin de rester dans le navigateur.
  update(entry.id, error ? { status: 'local', error } : { status: 'version', html: '' });
}

/** Reconnexion du compte : ses textes gardés dans le navigateur sont de nouveau envoyés ; envoyés, ils en sont
 * retirés (avis « enregistré dans l'historique », pour cette page seulement). Renvoie le nombre de textes restants. */
export function retryLocal() {
  // Un seul onglet à la fois (Web Locks) : deux onglets ouverts à la connexion enverraient chacun le même texte, soit
  // deux versions identiques ; le second relit ensuite la liste, d'où le premier a retiré ce qu'il a envoyé.
  const run = async () => {
    for (const entry of localEdits().filter((e) => !sending.has(e.id))) {
      const error = (await stillThere(entry)) || await send(entry);
      if (error) {
        update(entry.id, { error });
      } else {
        remove([entry.id]);
        recovered = [...recovered, { ...entry, html: '', status: 'recovered', memoryOnly: false }];
        notify();
      }
    }
    return localEdits().length;
  };
  return navigator.locks?.request ? navigator.locks.request(LOST_KEY, run) : run();
}

/** Avant un nouvel envoi (reconnexion) : la fiche est-elle toujours celle d'où vient le texte ? Supprimée entre-temps,
 * son numéro peut avoir été repris par une autre fiche (SQLite, tant que les tables n'ont pas AUTOINCREMENT) : le
 * texte n'y est pas envoyé. Renvoie le message d'erreur, ou null. */
async function stillThere(entry) {
  try {
    const doc = await withTimeout(api.getDocument(entry.docId), 10000);
    // Même campagne, et même date de création que la fiche d'où vient le texte (gardée avec lui).
    const reused = (entry.campaignId != null && Number(doc.campaign_id) !== Number(entry.campaignId))
      || (entry.created && doc.created_at !== entry.created);
    return reused ? t('la fiche a été supprimée') : null;
  } catch (err) {
    return err.status === 404 ? t('la fiche a été supprimée, ou votre accès retiré') : (err.message || String(err));
  }
}

mutate((list) => list);   // au chargement : effacement des textes de plus de KEEP_DAYS jours

/** Compte connecté (null : écran de connexion), appelé par App à chaque changement de session. */
export function setSessionUser(user) {
  const next = user ? { id: user.id, username: user.username, instance: user.instance } : null;
  const changed = JSON.stringify(next) !== JSON.stringify(account);
  account = next;
  if (!changed) return;
  if (!next) recovered = [];
  mutate((list) => list);   // effacement des textes trop anciens
  if (next) {
    retryLocal();
    // Envoi interrompu par un rechargement il y a moins de 30 s (session fermée, par exemple) : encore « en cours »
    // ci-dessus, compté comme gardé dans le navigateur ensuite.
    setTimeout(() => { if (account === next) retryLocal(); }, 31000);
  }
}

/** Attend la fin des enregistrements de modifications abandonnées en cours. */
const settled = () => (pending ? new Promise((resolve) => { waiters.push(resolve); }) : Promise.resolve());

// --- Autre version de Maze sur le serveur (H3) --------------------------------------------------

const RELOAD_KEY = 'maze:version-reload';
let stale = null;  // { version } du serveur

export const staleClient = () => stale;

/** Recharge la page, sauf si elle vient d'être rechargée pour la même raison (pas de boucle). */
export function reloadForUpdate(force = false) {
  let last = 0;
  try { last = Number(window.sessionStorage.getItem(RELOAD_KEY)) || 0; } catch { /* indisponible */ }
  if (!force && Date.now() - last < 60000) return false;
  try { window.sessionStorage.setItem(RELOAD_KEY, String(Date.now())); } catch { /* indisponible */ }
  window.location.reload();
  return true;
}

window.addEventListener(STALE_EVENT, async (e) => {
  if (stale) return;
  stale = { version: e.detail?.version ?? null };
  notify();
  await settled();
  // Saisie en cours dans un formulaire : rien n'est rechargé, le bandeau invite à la recopier puis à recharger.
  if (!unsaved.size) reloadForUpdate();
});
