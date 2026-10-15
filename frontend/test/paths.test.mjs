// Construction et tests de l'interface depuis un dossier dont le chemin contient une espace et un accent
// (audit FRONT-8 : new URL(import.meta.url).pathname gardait %20 et %C3%A9, et la forme /C:/… sous Windows).
// Copie de frontend/ (sans node_modules ni dist) dans « Mes Documents/Maze é/frontend », node_modules relié.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { FRONTEND, NODE_DIRS } from './helpers.mjs';

const modules = NODE_DIRS.find((dir) => existsSync(path.join(dir, 'esbuild', 'package.json')));
const opts = modules ? {} : { skip: 'esbuild introuvable (npm ci dans frontend/, ou MAZE_NODE_PATHS)' };

test('build.mjs et les tests depuis « Mes Documents/Maze é »', { ...opts, timeout: 180000 }, (t) => {
  const work = mkdtempSync(path.join(tmpdir(), 'maze-chemin-'));
  t.after(() => rmSync(work, { recursive: true, force: true }));
  const copy = path.join(work, 'Mes Documents', 'Maze é', 'frontend');
  mkdirSync(copy, { recursive: true });
  cpSync(FRONTEND, copy, {
    recursive: true,
    filter: (src) => !['node_modules', 'dist'].includes(path.basename(src)) || path.dirname(src) !== FRONTEND,
  });
  // Paquets du projet : même dossier que pour les autres tests (frontend/node_modules en intégration continue).
  symlinkSync(modules, path.join(copy, 'node_modules'), 'dir');
  // Sans NODE_TEST_CONTEXT (posé par node --test) : sinon le second node --test répond dans le format interne.
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  const node = (...args) => spawnSync(process.execPath, args, { cwd: copy, encoding: 'utf8', env });

  const built = node('build.mjs');
  assert.equal(built.status, 0, `${built.stdout}\n${built.stderr}`);
  const index = readFileSync(path.join(copy, 'dist', 'index.html'), 'utf8');
  const js = index.match(/src="\/(assets\/[^"]+\.js)"/)?.[1];
  assert.ok(js && existsSync(path.join(copy, 'dist', js)), index);

  const fingerprint = node('build.mjs', '--schema-fingerprint');
  assert.equal(fingerprint.status, 0, fingerprint.stderr);
  const expected = JSON.parse(readFileSync(path.join(FRONTEND, 'src/editor/schema-version.json'), 'utf8')).fingerprint;
  assert.equal(fingerprint.stdout.trim(), expected);

  // helpers.mjs (FRONTEND, regroupement par esbuild) depuis ce chemin.
  const tests = node('--test', 'test/schema.test.mjs', 'test/util.test.mjs');
  assert.equal(tests.status, 0, `${tests.stdout}\n${tests.stderr}`);
  assert.match(tests.stdout, /# fail 0/);
});
