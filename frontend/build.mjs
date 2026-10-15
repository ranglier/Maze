// Build de l'interface avec esbuild : dist/index.html + dist/assets/*-[hash].{js,css}
import { build, context } from 'esbuild';
import { readFile, writeFile, rm, mkdir, cp } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { schemaFingerprint } from './schema-fingerprint.mjs';

// fileURLToPath, pas URL.pathname : chemin décodé (espaces, accents) et valable sous Windows (audit FRONT-8).
const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, 'dist');
const watch = process.argv.includes('--watch');
// Chemins de modules supplémentaires (utile hors Docker, sans node_modules local), séparés comme PATH (« ; » sous Windows).
const nodePaths = (process.env.MAZE_NODE_PATHS || '').split(path.delimiter).filter(Boolean);

// Version du schéma de l'éditeur (audit H3, voir schema-fingerprint.mjs) : injectée dans l'interface, qui
// l'annonce au serveur (WebSocket et en-tête X-Maze-Version). Schéma changé sans nouvelle version : échec.
const schemaInfo = JSON.parse(await readFile(path.join(root, 'src/editor/schema-version.json'), 'utf8'));
const fingerprint = await schemaFingerprint(build, root, nodePaths);
if (process.argv.includes('--schema-fingerprint')) {
  console.log(fingerprint);
  process.exit(0);
}
if (fingerprint !== schemaInfo.fingerprint) {
  const message = `Le schéma de l'éditeur a changé (empreinte ${fingerprint}, attendue ${schemaInfo.fingerprint}) : `
    + 'augmentez "version" dans src/editor/schema-version.json, mettez "fingerprint" à la nouvelle empreinte, et la '
    + 'même version dans SCHEMA_VERSION de backend/maze/collab.py. Les onglets ouverts sur l\'ancienne version '
    + 'seront rechargés à la mise à jour, au lieu de supprimer chez tous les MJ les blocs qu\'ils ne connaissent pas.';
  if (!watch) {
    console.error(message);
    process.exit(1);
  }
  console.warn(message);
}

const writeIndex = async (metafile) => {
  const outputs = Object.keys(metafile.outputs);
  const js = outputs.find((f) => f.endsWith('.js'));
  const css = outputs.find((f) => f.endsWith('.css'));
  const rel = (f) => '/' + path.relative(dist, path.join(root, f)).split(path.sep).join('/');
  let html = await readFile(path.join(root, 'index.html'), 'utf8');
  html = html
    .replace('<!--CSS-->', css ? `<link rel="stylesheet" href="${rel(css)}">` : '')
    .replace('<!--JS-->', `<script type="module" src="${rel(js)}"></script>`);
  await writeFile(path.join(dist, 'index.html'), html);
};

const options = {
  // Chemins du metafile relatifs au dossier frontend, quel que soit le dossier de lancement.
  absWorkingDir: root,
  entryPoints: [path.join(root, 'src/main.jsx')],
  bundle: true,
  outdir: path.join(dist, 'assets'),
  entryNames: watch ? '[name]' : '[name]-[hash]',
  format: 'esm',
  minify: !watch,
  sourcemap: watch ? 'inline' : false,
  jsx: 'automatic',
  target: ['es2020'],
  metafile: true,
  nodePaths,
  logLevel: 'info',
  define: {
    'process.env.NODE_ENV': JSON.stringify(watch ? 'development' : 'production'),
    __MAZE_SCHEMA_VERSION__: JSON.stringify(schemaInfo.version),
  },
  plugins: [{
    name: 'index-html',
    setup(b) {
      b.onEnd(async (result) => { if (result.metafile) await writeIndex(result.metafile); });
    },
  }],
};

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(path.join(root, 'public'), dist, { recursive: true });

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
} else {
  await build(options);
}
