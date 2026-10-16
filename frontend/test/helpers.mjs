// Aides des tests de l'interface (node --test ; ce fichier n'est pas lui-même un test).
//
// Modules purs : importés tels quels. Modules qui dépendent de paquets npm (prosemirror…) : regroupés par esbuild
// (dépendance de développement du projet) avec les dossiers node_modules trouvés dans MAZE_NODE_PATHS, NODE_PATH ou
// frontend/node_modules. Fonctions qui ont besoin d'un vrai DOM (conversions Markdown et HTML) : exécutées dans
// Chromium par Playwright, module global des parcours navigateur (pas une dépendance du projet) ; sans lui, ces tests
// sont sautés, sauf avec MAZE_TEST_BROWSER=1 (intégration continue) où son absence est une erreur.
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { i18nCatalogsPlugin } from '../i18n-catalogs.mjs';

const require = createRequire(import.meta.url);
export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const NODE_DIRS = [...(process.env.MAZE_NODE_PATHS || '').split(path.delimiter),
  ...(process.env.NODE_PATH || '').split(path.delimiter),
  path.join(FRONTEND, 'node_modules')].filter(Boolean);

export function load(name) {
  for (const dir of NODE_DIRS) {
    try { return require(path.join(dir, name)); } catch { /* suivant */ }
  }
  return null;
}

/** Regroupe `source` (code ESM, chemins relatifs à frontend/) et l'importe. */
export async function bundle(source, { platform = 'node' } = {}) {
  const esbuild = load('esbuild');
  if (!esbuild) throw new Error('esbuild introuvable (npm ci dans frontend/, ou MAZE_NODE_PATHS)');
  const result = await esbuild.build({
    absWorkingDir: FRONTEND, stdin: { contents: source, resolveDir: FRONTEND, loader: 'js' }, bundle: true,
    write: false, format: 'esm', platform, nodePaths: NODE_DIRS, logLevel: 'silent', jsx: 'automatic',
    outdir: path.join(FRONTEND, 'test-bundle'),
    define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [i18nCatalogsPlugin()],   // catalogues de traduction dans le bundle, comme build.mjs
  });
  const code = result.outputFiles[0].text;
  if (platform === 'browser') return code;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}

/** Navigateur pour les tests qui ont besoin du DOM, ou { skip } avec la raison. */
export async function browserPage() {
  const playwright = load('playwright');
  if (!playwright) {
    if (process.env.MAZE_TEST_BROWSER === '1') throw new Error('Playwright introuvable (NODE_PATH ?)');
    return { skip: 'Playwright introuvable (NODE_PATH) : tests du DOM sautés' };
  }
  const browser = await playwright.chromium.launch();
  // Navigateur en français : l'interface suit la langue du navigateur (src/i18n), les attentes sont en français.
  const page = await browser.newPage({ locale: 'fr-FR' });
  return { browser, page };
}

/** Stockage du navigateur en mémoire (localStorage, sessionStorage) ; `broken` : chaque écriture échoue. */
export function memoryStorage({ broken = false } = {}) {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { if (broken) throw new Error('QuotaExceededError'); map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => map.clear(),
    get length() { return map.size; },
    map,
  };
}

/** `window` minimal pour les modules qui en lisent quelques propriétés au chargement. */
export function fakeWindow(extra = {}) {
  const target = new EventTarget();
  const win = {
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
    localStorage: memoryStorage(),
    sessionStorage: memoryStorage(),
    location: { reloads: 0, reload() { this.reloads += 1; } },
    ...extra,
  };
  globalThis.window = win;
  if (typeof globalThis.CustomEvent === 'undefined') {
    globalThis.CustomEvent = class extends Event {
      constructor(type, init) { super(type, init); this.detail = init?.detail; }
    };
  }
  return win;
}

/** Générateur pseudo-aléatoire reproductible (mulberry32). */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
