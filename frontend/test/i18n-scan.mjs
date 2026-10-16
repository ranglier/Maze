// Analyse statique de la traduction de l'interface (pas un fichier de test : utilisé par i18n.test.mjs et
// i18n-pending.test.mjs, et en ligne de commande).
//
//   node test/i18n-scan.mjs --missing [zone]   entrées manquantes des catalogues en/, par zone (JSON à compléter)
//   node test/i18n-scan.mjs --report [fichier] textes français écrits en dur, par fichier
//   node test/i18n-scan.mjs --format           réécrit les catalogues (triés, une entrée par ligne) et la liste
//                                              test/i18n-pending.json dans leur forme attendue
//   node test/i18n-scan.mjs --zones            zone de chaque fichier de src/ (tableau du README de src/i18n)
//
// Chaque fichier .js / .jsx de src/ est compilé par esbuild (JSX transformé en appels __jsx(...), commentaires
// retirés, carte des sources pour retrouver les lignes), puis ses littéraux sont classés : clés de t, tn, pt et N_,
// textes visibles (enfants JSX, attributs title, placeholder, aria-label, label…, arguments de toast, confirm…) et
// autres. Esbuild est une dépendance de développement du projet (MAZE_NODE_PATHS, NODE_PATH ou node_modules).
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { load } from './helpers.mjs';

export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const SRC = path.join(FRONTEND, 'src');
export const I18N = path.join(SRC, 'i18n');
export const PENDING_FILE = path.join(FRONTEND, 'test', 'i18n-pending.json');
export const SOURCE_LOCALE = 'fr';

// --- Zones ------------------------------------------------------------------------------------------------------
// Chaque fichier de src/ appartient à une seule zone (catalogue en/<zone>.json, agent ou contributeur qui la traduit).
// Tableau reproduit dans src/i18n/README.md (node test/i18n-scan.mjs --zones).
export const ZONES = ['common', 'admin', 'scenarios', 'places', 'characters'];
const ZONE_RULES = {
  common: ['App.jsx', 'main.jsx', 'api.js', 'constants.js', 'util.js', 'viewport.js', 'markdown.js', 'relationTypes.js',
    'cast.js', 'styles.css', 'i18n/', 'components/ui.jsx', 'components/UserMenu.jsx', 'components/LoginScreen.jsx',
    'components/MoveDialog.jsx', 'components/RichField.jsx', 'components/PlaceIcon.jsx', 'components/treeDrag.js',
    'components/mentionEntities.js'],
  admin: ['components/AdminPage.jsx', 'components/ClaudeSection.jsx', 'components/ImagesSection.jsx',
    'components/FoundrySection.jsx', 'components/CampaignMenu.jsx', 'components/SessionNotices.jsx',
    'components/SearchPalette.jsx', 'session.js'],
  scenarios: ['components/ScenariosView.jsx', 'components/scenarios/', 'editor/', 'components/DocDialogs.jsx',
    'components/HistoryPanel.jsx', 'components/CommentsPanel.jsx', 'components/ScenarioFilter.jsx',
    'components/TimelineView.jsx', 'timeline.js'],
  places: ['components/PlacesView.jsx', 'components/PlaceTree.jsx', 'components/PlaceCrumbs.jsx',
    'components/MapsView.jsx', 'components/maps/', 'places.js'],
  characters: ['components/CharactersView.jsx', 'components/EntityPanel.jsx', 'components/EntryNotes.jsx',
    'components/EntryDraft.jsx', 'components/Workspace.jsx', 'components/Gallery.jsx', 'components/Lightbox.jsx',
    'components/ImagePicker.jsx', 'components/CreateDialog.jsx', 'components/RelationDialog.jsx',
    'components/RelationPanel.jsx', 'components/RelationTypePicker.jsx', 'components/Legend.jsx', 'graph/', 'fields.js',
    'entry-fields.generated.js'],
};
export const ZONE_PATTERNS = ZONE_RULES;

/** Zones d'un fichier (chemin relatif à src/, séparateur /) : une seule attendue. */
export function zonesOf(rel) {
  return ZONES.filter((zone) => ZONE_RULES[zone].some((r) => (r.endsWith('/') ? rel.startsWith(r) : rel === r)));
}

/** Fichiers de src/ (chemins relatifs, séparateur /), triés. */
export function allFiles(dir = SRC, prefix = '') {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix + entry.name;
    if (entry.isDirectory()) out.push(...allFiles(path.join(dir, entry.name), `${rel}/`));
    else out.push(rel);
  }
  return out.sort();
}

export const sourceFiles = () => allFiles().filter((f) => /\.(js|jsx|mjs)$/.test(f));

// Registre des champs, généré depuis le serveur (backend/maze/entries.py) : ses libellés sont traduits dans
// l'interface (zone characters), à l'affichage. Ils comptent comme des clés marquées (N_), et le fichier n'est pas
// soumis au contrôle des textes en dur (il ne se modifie pas à la main).
export const GENERATED = new Set(['entry-fields.generated.js']);

// --- Catalogues ---------------------------------------------------------------------------------------------------

export function catalogLocales() {
  return readdirSync(I18N, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^[a-z]{2,3}$/.test(d.name)).map((d) => d.name).sort();
}

/** { locale: { zone: { clé: valeur } } } ; erreurs de lecture dans `errors`. */
export function readCatalogs(errors = []) {
  const out = {};
  for (const locale of catalogLocales()) {
    out[locale] = {};
    for (const file of readdirSync(path.join(I18N, locale)).filter((f) => f.endsWith('.json')).sort()) {
      const zone = file.replace(/\.json$/, '');
      const full = path.join(I18N, locale, file);
      try {
        const data = JSON.parse(readFileSync(full, 'utf8'));
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('un objet JSON attendu');
        out[locale][zone] = data;
      } catch (e) {
        errors.push(`${locale}/${file} : ${e.message}`);
        out[locale][zone] = {};
      }
    }
  }
  return out;
}

export const formatCatalog = (data) => `${JSON.stringify(Object.fromEntries(Object.keys(data).sort()
  .map((k) => [k, data[k]])), null, 2)}\n`;

export const placeholders = (text) => new Set([...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
export const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));

// --- Compilation et lecture des littéraux -----------------------------------------------------------------------

let esbuild = null;
function compiler() {
  esbuild ??= load('esbuild');
  if (!esbuild) throw new Error('esbuild introuvable (npm ci dans frontend/, ou MAZE_NODE_PATHS)');
  return esbuild;
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
/** Lignes d'origine (0…) des positions de la sortie : lines[ligne générée] = [[colonne, ligne d'origine]…]. */
function decodeMappings(mappings) {
  const lines = [];
  let srcLine = 0;
  for (const text of mappings.split(';')) {
    const segs = [];
    let col = 0;
    for (const seg of text.split(',')) {
      if (!seg) continue;
      const values = [];
      let value = 0;
      let shift = 0;
      for (const ch of seg) {
        const digit = B64.indexOf(ch);
        value += (digit & 31) << shift;
        if (digit & 32) shift += 5;
        else {
          values.push(value & 1 ? -(value >> 1) : value >> 1);
          value = 0;
          shift = 0;
        }
      }
      col += values[0];
      if (values.length >= 4) {
        srcLine += values[2];
        segs.push([col, srcLine]);
      }
    }
    lines.push(segs);
  }
  return lines;
}

function unescape(body) {
  return body.replace(/\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])/g, (m, e) => {
    if (e[0] === 'u' && e[1] === '{') return String.fromCodePoint(parseInt(e.slice(2, -1), 16));
    if (e[0] === 'u' && e.length === 5) return String.fromCharCode(parseInt(e.slice(1), 16));
    if (e[0] === 'x' && e.length === 3) return String.fromCharCode(parseInt(e.slice(1), 16));
    return { n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', 0: '\0', '\n': '', '\r\n': '' }[e] ?? e;
  });
}

const REGEX_AFTER = new Set(['(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '-', '*', '%', '<', '>',
  '~', '^', '=>', 'return', 'typeof', 'case', 'in', 'of', 'new', 'delete', 'void', 'throw', 'else', 'do', 'yield',
  'await', '&&', '||', '??', '==', '===', '!=', '!==']);

/** Jetons du code compilé : { type: 'id' | 'str' | 'num' | 'punct' | 'regex', value, line, col, template }. */
export function tokenize(code) {
  const tokens = [];
  let i = 0;
  let line = 0;
  let lineStart = 0;
  const braces = [];   // pile : '{' ordinaire ou '${' (expression de gabarit)
  const push = (type, value, start, extra) => tokens.push({ type, value, line, col: start - lineStart, ...extra });
  const prevSignificant = () => tokens[tokens.length - 1];
  const newline = (pos) => { line += 1; lineStart = pos + 1; };

  const readTemplate = (first) => {   // i pointe après ` (first) ou après } fermant une expression
    let start = i;
    let raw = '';
    while (i < code.length) {
      const ch = code[i];
      if (ch === '\\') { raw += code.slice(i, i + 2); if (code[i + 1] === '\n') newline(i + 1); i += 2; continue; }
      if (ch === '`') {
        push('str', unescape(raw), start, { template: true, whole: first });
        i += 1;
        return;
      }
      if (ch === '$' && code[i + 1] === '{') {
        push('str', unescape(raw), start, { template: true, whole: false });
        braces.push('${');
        push('punct', '${', i);
        i += 2;
        return;
      }
      if (ch === '\n') newline(i);
      raw += ch;
      i += 1;
    }
    start = i;
  };

  while (i < code.length) {
    const ch = code[i];
    if (ch === '\n') { newline(i); i += 1; continue; }
    if (/\s/.test(ch)) { i += 1; continue; }
    if (ch === '/' && code[i + 1] === '/') { while (i < code.length && code[i] !== '\n') i += 1; continue; }
    if (ch === '/' && code[i + 1] === '*') {
      const end = code.indexOf('*/', i + 2);
      for (let k = i; k < (end < 0 ? code.length : end); k += 1) if (code[k] === '\n') newline(k);
      i = end < 0 ? code.length : end + 2;
      continue;
    }
    if (ch === '"' || ch === "'") {
      const start = i;
      let j = i + 1;
      while (j < code.length && code[j] !== ch) { if (code[j] === '\\') j += 1; j += 1; }
      push('str', unescape(code.slice(i + 1, j)), start, {});
      i = j + 1;
      continue;
    }
    if (ch === '`') { i += 1; readTemplate(true); continue; }
    if (/[A-Za-z_$À-￿]/.test(ch)) {
      const start = i;
      while (i < code.length && /[\w$À-￿]/.test(code[i])) i += 1;
      push('id', code.slice(start, i), start);
      continue;
    }
    if (/[0-9]/.test(ch) || (ch === '.' && /[0-9]/.test(code[i + 1]))) {
      const start = i;
      while (i < code.length && /[\w.]/.test(code[i])) i += 1;
      push('num', code.slice(start, i), start);
      continue;
    }
    if (ch === '/') {
      const prev = prevSignificant();
      const regexOk = !prev || (prev.type === 'punct' && REGEX_AFTER.has(prev.value) && prev.value !== ')' && prev.value !== ']')
        || (prev.type === 'id' && REGEX_AFTER.has(prev.value));
      if (regexOk) {
        const start = i;
        let j = i + 1;
        let inClass = false;
        while (j < code.length) {
          const c = code[j];
          if (c === '\\') { j += 2; continue; }
          if (c === '[') inClass = true;
          else if (c === ']') inClass = false;
          else if (c === '/' && !inClass) break;
          else if (c === '\n') break;
          j += 1;
        }
        j += 1;
        while (j < code.length && /[a-z]/i.test(code[j])) j += 1;
        push('regex', code.slice(start, j), start);
        i = j;
        continue;
      }
    }
    const three = code.slice(i, i + 3);
    const two = code.slice(i, i + 2);
    if (['===', '!==', '...', '**=', '??=', '&&=', '||=', '>>>'].includes(three)) { push('punct', three, i); i += 3; continue; }
    if (['=>', '?.', '==', '!=', '<=', '>=', '&&', '||', '??', '++', '--', '+=', '-=', '*=', '/=', '**', '<<', '>>']
      .includes(two) && !(two === '?.' && /[0-9]/.test(code[i + 2]))) {
      push('punct', two, i);
      i += 2;
      continue;
    }
    if (ch === '{') braces.push('{');
    if (ch === '}') {
      const top = braces.pop();
      if (top === '${') { push('punct', '}$', i); i += 1; readTemplate(false); continue; }
    }
    push('punct', ch, i);
    i += 1;
  }
  return tokens;
}

// --- Classement des littéraux -------------------------------------------------------------------------------------

const TRANSLATORS = { t: [0], tn: [0, 1], pt: [1], N_: [0] };
const VISIBLE_KEYS = new Set(['title', 'placeholder', 'aria-label', 'aria-description', 'aria-roledescription', 'label',
  'alt', 'hint', 'message', 'confirmLabel', 'intro', 'tooltip', 'emptyLabel']);
const VISIBLE_CALLS = new Set(['toast', 'confirm', 'alert', 'prompt', 'setError', 'setMessage', 'setNotice', 'run',
  'Error', 'ApiError', 'success', 'fail']);
const LOCALE_CALLS = /(^|\.)(localeCompare|toLocale\w*|DateTimeFormat|NumberFormat|Collator|PluralRules|RelativeTimeFormat|ListFormat|DisplayNames)$/;
const IGNORED_CALLS = /(^|\.)(log|warn|error|info|debug|querySelector|querySelectorAll|closest|matches|require|getItem|setItem|removeItem)$/;
const KEYWORDS = new Set(['if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'typeof', 'with']);

const ACCENT = /[À-ÖØ-öø-ÿŒœ]/;
const TYPO = /[’«»…]/;
const FRENCH_WORDS = ['le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'et', 'ou', 'au', 'aux', 'pour', 'par', 'avec',
  'dans', 'sur', 'sous', 'chez', 'vers', 'ce', 'cet', 'cette', 'ces', 'sa', 'ses', 'leur', 'leurs', 'votre', 'vos',
  'vous', 'nous', 'est', 'sont', 'pas', 'ne', 'aucun', 'aucune', 'tout', 'tous', 'toute', 'toutes', 'qui', 'que',
  'quand', 'comme', 'puis', 'fiche', 'fiches', 'lieu', 'lieux', 'carte', 'cartes', 'campagne', 'campagnes',
  'personnage', 'personnages', 'modifier', 'supprimer', 'ajouter', 'enregistrer', 'annuler', 'fermer', 'ouvrir',
  'retirer', 'choisir', 'rechercher', 'importer', 'exporter', 'copier', 'restaurer', 'sauvegarde', 'sauvegardes',
  'compte', 'comptes', 'connexion', 'nom', 'titre', 'erreur', 'nouveau', 'nouvelle', 'oui', 'non', 'PNJ', 'MJ',
  'introuvable', 'impossible', 'chargement'];
const FRENCH_WORD = new RegExp(`(^|[^\\p{L}\\p{N}_-])(${FRENCH_WORDS.join('|')})(?=$|[^\\p{L}\\p{N}_-])`, 'iu');

/** Texte qui a l'air français : `strict` hors des emplacements visibles (moins de faux positifs). */
export function looksFrench(text, strict = false) {
  if (!/\p{L}/u.test(text)) return false;
  // Identifiant ou chemin (clé de route, nom de classe, adresse) : pas un texte affiché.
  if (/^[#/.]/.test(text) || /^[a-z][a-z0-9_:.-]*$/.test(text)) return false;
  if (!strict) return ACCENT.test(text) || TYPO.test(text) || FRENCH_WORD.test(text);
  return (ACCENT.test(text) && /\p{L}{2}/u.test(text)) || TYPO.test(text)
    || (FRENCH_WORD.test(text) && /\S\s+\S/.test(text));
}

function calleeBefore(tokens, k) {   // tokens[k] est '(' ; nom de la fonction appelée (a.b.c), ou null
  let j = k - 1;
  if (tokens[j]?.value === '?.') j -= 1;
  if (tokens[j]?.type !== 'id') return null;
  const parts = [tokens[j].value];
  while (tokens[j - 1]?.value === '.' || tokens[j - 1]?.value === '?.') {
    if (tokens[j - 2]?.type !== 'id') break;
    parts.unshift(tokens[j - 2].value);
    j -= 2;
  }
  if (KEYWORDS.has(parts[parts.length - 1])) return null;
  if (tokens[j - 1]?.value === 'function') return null;   // déclaration : paramètres, pas un appel
  return parts.join('.');
}

/**
 * Analyse d'un fichier : { keys, problems, findings, imports, shadows }.
 *  keys      : [{ fn, key, plural?, context?, line }] (clés de catalogue, contexte compris pour pt : « ctx|texte »)
 *  problems  : appels mal formés (concaténation, gabarit avec ${}, paramètres manquants)
 *  findings  : textes français écrits en dur [{ line, text, why }]
 */
export function analyze(rel, source = readFileSync(path.join(SRC, rel), 'utf8')) {
  const out = { keys: [], problems: [], findings: [], importsT: new Set(), shadows: [] };
  if (GENERATED.has(rel)) {
    for (const m of source.matchAll(/"label": "((?:[^"\\]|\\.)*)"/g)) {
      out.keys.push({ fn: 'N_', key: JSON.parse(`"${m[1]}"`), line: source.slice(0, m.index).split('\n').length });
    }
    return out;
  }
  const result = compiler().transformSync(source, {
    loader: 'jsx', jsx: 'transform', jsxFactory: '__jsx', jsxFragment: '__jsxFrag', charset: 'utf8',
    sourcemap: 'external', sourcefile: rel, target: 'esnext', format: 'esm',
  });
  const map = decodeMappings(JSON.parse(result.map).mappings);
  const srcLines = source.split('\n');
  const origLine = (tok) => {
    const segs = map[tok.line] || [];
    let found = null;
    for (const [col, src] of segs) { if (col <= tok.col) found = src; else break; }
    if (found === null) {
      for (let l = tok.line - 1; l >= 0 && found === null; l -= 1) found = map[l]?.at(-1)?.[1] ?? null;
    }
    return (found ?? 0) + 1;
  };
  const ignored = (line) => /i18n-ignore/.test(srcLines[line - 1] || '') || /i18n-ignore-next-line/.test(srcLines[line - 2] || '');

  for (const m of result.code.matchAll(/import\s*\{([^}]*)\}\s*from\s*"[^"]*i18n\/(?:index|core)\.js"/g)) {
    for (const name of m[1].split(',').map((s) => s.trim().split(/\s+as\s+/).pop())) out.importsT.add(name);
  }

  const tokens = tokenize(result.code);
  const consumed = new Set();

  // Clés : t('…'), tn('…', '…', n), pt('ctx', '…'), N_('…').
  for (let k = 0; k < tokens.length - 2; k += 1) {
    const tok = tokens[k];
    if (tok.type !== 'id' || !TRANSLATORS[tok.value] || tokens[k + 1].value !== '(') continue;
    if (tokens[k - 1]?.value === '.' || tokens[k - 1]?.value === 'function' || tokens[k - 1]?.value === '?.') continue;
    const fn = tok.value;
    const args = [];
    let j = k + 2;
    const literal = (t) => t?.type === 'str' && (!t.template || t.whole);
    while (args.length < (fn === 'tn' || fn === 'pt' ? 2 : 1)) {
      const a = tokens[j];
      if (literal(a)) {
        args.push(a);
        consumed.add(a);
        const next = tokens[j + 1];
        if (next?.value === '+') out.problems.push(`${rel}:${origLine(a)} : ${fn}() avec une concaténation (« ${a.value} » + …) : un seul texte, avec des paramètres {x}`);
        j += 2;   // texte puis , ou )
        if (next?.value !== ',' && args.length < (fn === 'tn' || fn === 'pt' ? 2 : 1)) break;
      } else {
        if (a?.type === 'str' && a.template) out.problems.push(`${rel}:${origLine(a)} : ${fn}() avec un gabarit \`…\${}…\` : texte fixe et paramètres {x} attendus`);
        break;
      }
    }
    const needed = fn === 'tn' || fn === 'pt' ? 2 : 1;
    if (args.length < needed) {
      if (fn !== 'pt' || args.length === 1) {
        // Argument dynamique (variable) : clé marquée ailleurs par N_ ; rien à extraire ici.
      }
      continue;
    }
    const line = origLine(args[0]);
    const after = tokens[j - 1];   // , ou ) après le dernier texte
    if (fn === 'pt') {
      if (!/^[\w-]+$/.test(args[0].value)) out.problems.push(`${rel}:${line} : contexte de pt() invalide « ${args[0].value} » (lettres, chiffres, - et _)`);
      out.keys.push({ fn, key: `${args[0].value}|${args[1].value}`, context: args[0].value, text: args[1].value, line });
      if (placeholders(args[1].value).size && after?.value !== ',') out.problems.push(`${rel}:${line} : pt('${args[0].value}', '${args[1].value}') sans paramètres`);
    } else if (fn === 'tn') {
      out.keys.push({ fn, key: args[0].value, plural: args[1].value, line });
    } else {
      out.keys.push({ fn, key: args[0].value, line });
      if (fn === 't' && placeholders(args[0].value).size && after?.value !== ',') out.problems.push(`${rel}:${line} : t('${args[0].value}') sans paramètres pour ${[...placeholders(args[0].value)].map((p) => `{${p}}`).join(', ')}`);
    }
  }

  // Variable locale nommée t, tn, pt ou N_ (paramètre, déclaration) dans un fichier qui importe cette fonction : elle
  // la masquerait. esbuild la renomme alors (t2, pt3…) : un tel nom absent du source d'origine la trahit. (Un fichier
  // qui n'importe pas la fonction garde ses variables, par exemple t en géométrie.)
  const reported = new Set();
  for (const tok of tokens) {
    const m = tok.type === 'id' && tok.value.match(/^(t|tn|pt|N_)(\d+)$/);
    if (!m || !out.importsT.has(m[1]) || new RegExp(`\\b${tok.value}\\b`).test(source)) continue;
    const line = origLine(tok);
    if (reported.has(`${m[1]}:${line}`)) continue;
    reported.add(`${m[1]}:${line}`);
    out.shadows.push(`${rel}:${line} : variable locale « ${m[1]} » (masque la fonction de traduction : renommez-la)`);
  }

  // Textes en dur : pile des parenthèses, crochets et accolades, avec la fonction appelée et la clé courante.
  const stack = [];
  for (let k = 0; k < tokens.length; k += 1) {
    const tok = tokens[k];
    const top = stack[stack.length - 1];
    if (tok.type === 'punct') {
      const v = tok.value;
      if (v === '(') stack.push({ open: '(', callee: calleeBefore(tokens, k), arg: 0 });
      else if (v === '[') stack.push({ open: '[' });
      else if (v === '{' || v === '${') stack.push({ open: v, key: null });
      else if (v === ')' || v === ']' || v === '}' || v === '}$') stack.pop();
      else if (v === ',' && top) { if (top.open === '(') top.arg += 1; if (top.open === '{') top.key = null; }
      else if (v === ':' && top?.open === '{') {
        const p = tokens[k - 1];
        const pp = tokens[k - 2]?.value;
        if ((p?.type === 'id' || p?.type === 'str' || p?.type === 'num') && (pp === '{' || pp === ',')) top.key = p.value;
      }
      continue;
    }
    if (tok.type !== 'str' || consumed.has(tok) || !tok.value.trim()) continue;
    const prev = tokens[k - 1];
    const next = tokens[k + 1];
    if (next?.value === ':' && (prev?.value === '{' || prev?.value === ',')) continue;   // clé d'objet
    if (prev?.value === 'from' || prev?.value === 'import') continue;
    if (['===', '!==', '==', '!='].includes(prev?.value) || ['===', '!==', '==', '!='].includes(next?.value)) continue;
    const frame = [...stack].reverse().find((f) => f.open !== '[' && f.open !== '${') || top;
    const callee = frame?.open === '(' ? frame.callee : null;
    if (callee && IGNORED_CALLS.test(callee)) continue;
    const line = origLine(tok);
    if (/^fr(-FR)?$/.test(tok.value) && callee && LOCALE_CALLS.test(callee)) {
      if (!ignored(line)) out.findings.push({ line, text: tok.value, why: 'langue écrite en dur (formats de src/i18n)' });
      continue;
    }
    if (callee === '__jsx' && frame.arg === 0) continue;   // nom de balise
    const visible = (callee === '__jsx' && frame.arg >= 2)
      || (frame?.open === '{' && frame.key && VISIBLE_KEYS.has(frame.key))
      || (callee && VISIBLE_CALLS.has(callee.split('.').pop()));
    if (!looksFrench(tok.value, !visible) || ignored(line)) continue;
    out.findings.push({ line, text: tok.value.trim().replace(/\s+/g, ' ').slice(0, 90), why: visible ? 'visible' : 'texte' });
  }
  return out;
}

// --- Ensemble du dépôt ----------------------------------------------------------------------------------------------

/** Analyse de tous les fichiers de src/ (hors src/i18n/, qui définit les fonctions). */
export function scanAll() {
  const files = {};
  for (const rel of sourceFiles()) {
    if (rel.startsWith('i18n/')) continue;
    files[rel] = analyze(rel);
  }
  return files;
}

export function readPending() {
  const text = readFileSync(PENDING_FILE, 'utf8');
  return { text, data: JSON.parse(text) };
}

export const pendingFiles = (data) => new Set(Object.values(data).flat());

/** Forme attendue de i18n-pending.json : les zones dans l'ordre, fichiers triés, un par ligne (fusions sans conflit :
 * chaque zone n'est modifiée que par celui qui la traduit, et deux zones sont séparées par des lignes inchangées). */
export const formatPending = (data) => `${JSON.stringify(Object.fromEntries(ZONES.map((z) => [z, [...(data[z] || [])]
  .sort()])), null, 2)}\n`;

/** Clés extraites -> fichiers qui les utilisent. */
export function usedKeys(files) {
  const used = new Map();
  for (const [rel, info] of Object.entries(files)) {
    for (const k of info.keys) {
      if (!used.has(k.key)) used.set(k.key, []);
      used.get(k.key).push({ rel, ...k });
    }
  }
  return used;
}

/** Entrées manquantes d'une langue : [{ rel, zone, key, plural, line }]. */
export function missingEntries(files, catalogs, locale) {
  const merged = Object.assign({}, ...Object.values(catalogs[locale] || {}));
  const out = [];
  for (const [rel, info] of Object.entries(files)) {
    for (const k of info.keys) {
      const value = merged[k.key];
      const ok = k.fn === 'tn' ? value && typeof value === 'object' : typeof value === 'string' && value.trim();
      if (!ok) out.push({ rel, zone: zonesOf(rel)[0], key: k.key, plural: k.plural, line: k.line });
    }
  }
  return out;
}

function zonesTable() {
  const rows = ZONES.map((zone) => `| ${zone} | ${ZONE_RULES[zone].map((r) => `\`${r}\``).join(', ')} |`);
  return ['| Zone | Files and folders of `frontend/src/` |', '|---|---|', ...rows].join('\n');
}

// --- Ligne de commande ----------------------------------------------------------------------------------------------

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [cmd, arg] = process.argv.slice(2);
  if (cmd === '--zones') {
    console.log(zonesTable());
    for (const f of allFiles()) if (zonesOf(f).length !== 1) console.log(`!! ${f} : ${zonesOf(f).join(', ') || 'aucune zone'}`);
  } else if (cmd === '--format') {
    for (const locale of catalogLocales()) {
      for (const file of readdirSync(path.join(I18N, locale)).filter((f) => f.endsWith('.json'))) {
        const full = path.join(I18N, locale, file);
        writeFileSync(full, formatCatalog(JSON.parse(readFileSync(full, 'utf8'))));
      }
    }
    const { data } = readPending();
    writeFileSync(PENDING_FILE, formatPending(data));
  } else if (cmd === '--missing') {
    const files = scanAll();
    const catalogs = readCatalogs();
    for (const locale of catalogLocales()) {
      const byZone = {};
      for (const m of missingEntries(files, catalogs, locale)) {
        if (arg && m.zone !== arg && m.rel !== arg) continue;
        (byZone[m.zone] ??= {})[m.key] = m.plural !== undefined ? { one: '', other: '' } : '';
      }
      for (const [zone, entries] of Object.entries(byZone)) {
        console.log(`// ${locale}/${zone}.json : ${Object.keys(entries).length} entrée(s) à ajouter`);
        console.log(JSON.stringify(entries, null, 2));
      }
    }
  } else if (cmd === '--report') {
    const files = arg ? { [arg]: analyze(arg) } : scanAll();
    for (const [rel, info] of Object.entries(files)) {
      for (const f of info.findings) console.log(`${rel}:${f.line} [${f.why}] ${f.text}`);
      for (const p of [...info.problems, ...info.shadows]) console.log(p);
    }
  } else {
    console.log('Usage : node test/i18n-scan.mjs --missing [zone|fichier] | --report [fichier] | --format | --zones');
  }
}
