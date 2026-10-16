// Cliquet de la traduction : aucun texte français écrit en dur dans src/ (enfants JSX, attributs title, placeholder,
// aria-label, label…, arguments de toast, confirm…, et autres littéraux qui ont l'air français), hors appel t(), sauf
// dans les fichiers de test/i18n-pending.json (pas encore traduits). La liste ne fait que raccourcir : un fichier
// traduit doit en sortir. Faux positif : commentaire « i18n-ignore » sur la ligne (ou « i18n-ignore-next-line » sur
// la ligne d'avant). Heuristique et détail des findings : test/i18n-scan.mjs (--report [fichier]).
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import {
  GENERATED, SRC, ZONES, formatPending, missingEntries, readCatalogs, readPending, scanAll, zonesOf,
} from './i18n-scan.mjs';

const files = scanAll();
const { text, data } = readPending();
const report = (list, title) => assert.deepEqual(list, [], `${title} :\n  ${list.join('\n  ')}`);

test('i18n-pending.json : zones dans l\'ordre, fichiers existants triés, un par ligne, chacun dans sa zone', () => {
  assert.deepEqual(Object.keys(data), ZONES);
  const problems = [];
  const seen = new Set();
  for (const zone of ZONES) {
    for (const rel of data[zone]) {
      if (seen.has(rel)) problems.push(`${rel} : en double`);
      seen.add(rel);
      if (!existsSync(path.join(SRC, rel))) problems.push(`${rel} : fichier introuvable dans src/`);
      else if (zonesOf(rel)[0] !== zone) problems.push(`${rel} : rangé dans ${zone}, zone ${zonesOf(rel)[0]}`);
    }
  }
  report(problems, 'liste des fichiers en attente');
  assert.equal(text, formatPending(data), 'forme attendue : node test/i18n-scan.mjs --format');
});

test('aucun texte français écrit en dur hors des fichiers en attente', () => {
  const pending = new Set(Object.values(data).flat());
  const found = [];
  for (const [rel, info] of Object.entries(files)) {
    if (pending.has(rel)) continue;
    for (const f of info.findings) found.push(`${rel}:${f.line} [${f.why}] ${f.text}`);
  }
  report(found, 'textes en dur (t() / tn() / pt(), ou « // i18n-ignore » pour un faux positif)');
});

test('un fichier traduit sort de la liste', () => {
  const catalogs = readCatalogs();
  const missing = new Set(missingEntries(files, catalogs, 'en').map((m) => m.rel));
  const done = Object.values(data).flat()
    .filter((rel) => files[rel] && !files[rel].findings.length && !missing.has(rel))
    .map((rel) => `${rel} : plus aucun texte en dur ni traduction manquante, à retirer de i18n-pending.json`);
  report(done, 'fichiers terminés');
  // Le registre des champs généré n'est jamais analysé pour les textes en dur (libellés traduits à l'affichage).
  for (const rel of GENERATED) assert.equal(files[rel]?.findings.length ?? 0, 0);
});
