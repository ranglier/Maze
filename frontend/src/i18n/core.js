// Traduction de l'interface, sans React : t, tn, pt, langue courante et formats (voir README.md du dossier).
// Importé tel quel par les tests Node (node --test) ; les composants importent ./index.js, qui ajoute useLocale.
//
// Style gettext : la phrase française sert de clé. Catalogues ./<langue>/<zone>.json (aucun pour le français, langue
// source) ; clé absente : texte français. Paramètres {nom} remplis par l'objet `params`.
import { CATALOGS } from './catalogs.js';

export const SOURCE_LOCALE = 'fr';
/** Langues de l'interface : le français, puis chaque dossier de catalogues (ajouter une langue = ajouter un dossier). */
export const LOCALES = [SOURCE_LOCALE, ...Object.keys(CATALOGS).filter((l) => l !== SOURCE_LOCALE).sort()];

const STORAGE_KEY = 'maze:locale';
const PLACEHOLDER = /\{(\w+)\}/g;
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

let current = SOURCE_LOCALE;
const listeners = new Set();
// Dernières préférences connues (compte, langue de l'instance), reprises par applyPreferences() sans argument.
let prefs = { account: '', fallback: SOURCE_LOCALE };

// --- Traduction ------------------------------------------------------------

const isNode = (v) => v !== null && typeof v === 'object';

/** Remplit {nom} avec params.nom. Un paramètre objet (élément React) : tableau de morceaux (index.js en fait un
 * fragment). Paramètre absent : {nom} laissé tel quel. */
export function interpolate(text, params) {
  if (!params) return text;
  const values = Object.values(params);
  if (!values.some(isNode)) {
    return text.replace(PLACEHOLDER, (m, name) => (has(params, name) ? String(params[name]) : m));
  }
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(PLACEHOLDER)) {
    if (!has(params, m[1])) continue;
    if (m.index > last) parts.push(text.slice(last, m.index));
    const value = params[m[1]];
    parts.push(isNode(value) ? value : String(value));
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function entry(key) {
  return current === SOURCE_LOCALE ? undefined : CATALOGS[current]?.[key];
}

/** Texte traduit : t('Enregistrer'), t('{n} lieux trouvés', { n }). */
export function t(text, params) {
  const found = entry(text);
  return interpolate(typeof found === 'string' && found ? found : text, params);
}

/** Texte selon le contexte, quand une même phrase française se traduit différemment selon l'endroit :
 * pt('nav', 'Carte'). Clé du catalogue : « nav|Carte ». */
export function pt(context, text, params) {
  const found = entry(`${context}|${text}`);
  return interpolate(typeof found === 'string' && found ? found : text, params);
}

const pluralRules = new Map();
function pluralCategory(locale, n) {
  if (!pluralRules.has(locale)) pluralRules.set(locale, new Intl.PluralRules(locale));
  return pluralRules.get(locale).select(Number(n));
}

/** Pluriel : tn('{n} lieu', '{n} lieux', n, { n }) (n est aussi ajouté aux paramètres). Catalogue : clé = forme au
 * singulier, valeur = { one, other… } (catégories d'Intl.PluralRules de la langue). */
export function tn(singular, plural, n, params) {
  const values = { n, ...params };
  const found = entry(singular);
  if (found && typeof found === 'object') {
    const form = found[pluralCategory(current, n)] ?? found.other;
    if (typeof form === 'string' && form) return interpolate(form, values);
  }
  return interpolate(pluralCategory(SOURCE_LOCALE, n) === 'one' ? singular : plural, values);
}

/** Marque un texte à traduire plus tard, à l'affichage (constantes de module) : renvoyé tel quel. */
export const N_ = (text) => text;

// --- Langue courante ---------------------------------------------------------

export const getLocale = () => current;

/** Code connu de l'interface pour une valeur (« en-US » donne « en »), sinon null. */
export function matchLocale(value) {
  if (typeof value !== 'string' || !value) return null;
  const code = value.trim().replace('_', '-').split('-')[0].toLowerCase();
  return LOCALES.includes(code) ? code : null;
}

// Langues du navigateur ; aucune hors d'une page (Node expose aussi navigator.language, d'après la langue du système :
// les tests Node restent ainsi en français, sauf langue choisie explicitement).
const navigatorLanguages = () => (typeof document === 'undefined' ? []
  : globalThis.navigator?.languages || [globalThis.navigator?.language]);

/** Première langue du navigateur que l'interface connaît, ou null. */
export function browserLocale(languages = navigatorLanguages()) {
  for (const lang of languages || []) {
    const found = matchLocale(lang);
    if (found) return found;
  }
  return null;
}

/** Ordre : réglage du compte, choix fait sur l'écran de connexion (ce navigateur), langue du navigateur, langue par
 * défaut de l'instance (MAZE_DEFAULT_LOCALE), français. */
export function resolveLocale({ account, stored, browser, fallback } = {}) {
  return matchLocale(account) || matchLocale(stored) || browserLocale(browser) || matchLocale(fallback)
    || SOURCE_LOCALE;
}

/** Choix mémorisé dans ce navigateur (écran de connexion, menu du compte) ; '' : automatique. */
export function storedLocale() {
  try { return globalThis.localStorage?.getItem(STORAGE_KEY) || ''; } catch { return ''; }
}

export function storeLocale(locale) {
  try {
    if (locale) globalThis.localStorage?.setItem(STORAGE_KEY, locale);
    else globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch { /* stockage indisponible : choix valable jusqu'au rechargement */ }
}

/** Change la langue affichée ; `<html lang>` suit ; les composants qui appellent useLocale() sont redessinés. */
export function setLocale(locale) {
  const next = matchLocale(locale) || SOURCE_LOCALE;
  if (globalThis.document?.documentElement) globalThis.document.documentElement.lang = next;
  if (next === current) return;
  current = next;
  for (const fn of listeners) fn(next);
}

/** Recalcule la langue d'après les préférences : `account` (user.locale), `fallback` (default_locale de
 * /api/auth/status). Sans argument : dernières préférences connues (après un choix sur l'écran de connexion). */
export function applyPreferences(next) {
  if (next) prefs = { account: next.account || '', fallback: next.fallback || SOURCE_LOCALE };
  setLocale(resolveLocale({ ...prefs, stored: storedLocale(), browser: undefined }));
  return current;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Nom d'une langue dans cette langue : « Français », « English ». */
export function languageName(locale) {
  try {
    const name = new Intl.DisplayNames([locale], { type: 'language' }).of(locale);
    return name ? name.charAt(0).toLocaleUpperCase(locale) + name.slice(1) : locale;
  } catch {
    return locale;
  }
}

// --- Formats (dates, nombres, tri) -------------------------------------------

/** Étiquette BCP 47 des formats : fr-FR pour le français (comme avant la traduction), sinon la variante régionale
 * du navigateur pour cette langue (en-GB, en-US…), à défaut la langue seule. */
export function formatLocale(locale = current) {
  if (locale === SOURCE_LOCALE) return 'fr-FR';
  for (const lang of navigatorLanguages()) {
    if (matchLocale(lang) === locale && /^[a-z]{2,3}-[A-Z]{2}$/.test(lang)) return lang;
  }
  return locale;
}

const formatters = new Map();
function cached(kind, options, make) {
  const tag = formatLocale();
  const key = `${kind}|${tag}|${JSON.stringify(options || {})}`;
  if (!formatters.has(key)) formatters.set(key, make(tag));
  return formatters.get(key);
}

/** Intl.DateTimeFormat de la langue courante (gardé en cache) : à créer au moment de l'affichage, jamais au
 * chargement du module (la langue peut changer). */
export const dateFormat = (options) => cached('date', options, (tag) => new Intl.DateTimeFormat(tag, options));

/** Date (objet Date, horodatage ou texte ISO) formatée dans la langue courante. */
export function formatDate(value, options = { dateStyle: 'medium' }) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? '' : dateFormat(options).format(date);
}

export const numberFormat = (options) => cached('number', options, (tag) => new Intl.NumberFormat(tag, options));
export const formatNumber = (n, options) => numberFormat(options).format(n);

/** « A, B et C » / « A, B and C ». */
export const formatList = (items, options = { type: 'conjunction' }) =>
  cached('list', options, (tag) => new Intl.ListFormat(tag, options)).format(items);

/** « il y a 5 minutes » / « 5 minutes ago » : relativeTime(-5, 'minute'). */
export const relativeTime = (value, unit, options = { numeric: 'auto' }) =>
  cached('relative', options, (tag) => new Intl.RelativeTimeFormat(tag, options)).format(value, unit);

/** Comparaison de textes pour un tri (à la place de a.localeCompare(b, 'fr')). */
export const compareText = (a, b, options) =>
  cached('collator', options, (tag) => new Intl.Collator(tag, options)).compare(a ?? '', b ?? '');

// Langue au chargement : choix mémorisé, puis navigateur ; affinée par App dès /api/auth/status (compte, instance).
setLocale(resolveLocale({ stored: storedLocale() }));
