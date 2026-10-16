// Catalogues de traduction de l'interface (src/i18n/<langue>/<zone>.json) face au code : chaque appel littéral
// t('…'), tn('…', '…', n), pt('ctx', '…') et N_('…') de src/ a sa traduction, aucune entrée n'est inutilisée, les
// paramètres {x} sont les mêmes des deux côtés, les pluriels ont toutes les formes de la langue. Fichiers encore
// listés dans test/i18n-pending.json : leurs traductions manquantes sont tolérées (traduction en cours).
// Analyse : test/i18n-scan.mjs (aussi en ligne de commande : --missing, --report, --format).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import {
  I18N, ZONES, allFiles, catalogLocales, formatCatalog, missingEntries, pendingFiles, placeholders, readCatalogs,
  readPending, sameSet, scanAll, usedKeys, zonesOf,
} from './i18n-scan.mjs';

const files = scanAll();
const errors = [];
const catalogs = readCatalogs(errors);
const pending = pendingFiles(readPending().data);
const used = usedKeys(files);
const report = (list, title) => assert.deepEqual(list, [], `${title} :\n  ${list.join('\n  ')}`);

test('catalogues lisibles : un objet JSON par fichier, zones connues, triés, une entrée par ligne', () => {
  report(errors, 'catalogues illisibles');
  assert.ok(catalogLocales().includes('en'));
  assert.ok(!catalogLocales().includes('fr'), 'pas de catalogue pour le français, langue source');
  const problems = [];
  for (const locale of catalogLocales()) {
    const zones = readdirSync(path.join(I18N, locale)).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5));
    for (const zone of zones) if (!ZONES.includes(zone)) problems.push(`${locale}/${zone}.json : zone inconnue (${ZONES.join(', ')})`);
    for (const zone of ZONES) {
      const file = path.join(I18N, locale, `${zone}.json`);
      if (!zones.includes(zone)) { problems.push(`${locale}/${zone}.json manquant`); continue; }
      if (readFileSync(file, 'utf8') !== formatCatalog(catalogs[locale][zone])) {
        problems.push(`${locale}/${zone}.json : clés non triées ou mise en forme différente (node test/i18n-scan.mjs --format)`);
      }
    }
  }
  report(problems, 'catalogues');
});

test('chaque fichier de src/ appartient à une seule zone', () => {
  report(allFiles().filter((f) => zonesOf(f).length !== 1).map((f) => `${f} : ${zonesOf(f).join(', ') || 'aucune'}`),
    'zones (ZONE_RULES de test/i18n-scan.mjs et tableau de src/i18n/README.md)');
});

test('appels bien formés : texte fixe, paramètres fournis, fonctions importées, pas de variable t locale', () => {
  const problems = [];
  for (const [rel, info] of Object.entries(files)) {
    problems.push(...info.problems, ...info.shadows);
    for (const fn of new Set(info.keys.map((k) => k.fn))) {
      if (!info.importsT.has(fn) && !rel.endsWith('.generated.js')) problems.push(`${rel} : ${fn}() utilisé sans import depuis i18n/index.js (ou core.js)`);
    }
  }
  report(problems, 'appels de traduction');
});

// Langues tenues complètes (les autres peuvent être partielles : repli sur le français, paramètres vérifiés).
const COMPLETE = ['en'];

test('chaque texte à traduire a sa traduction anglaise (fichiers hors i18n-pending.json)', () => {
  for (const locale of COMPLETE) {
    const missing = missingEntries(files, catalogs, locale).filter((m) => !pending.has(m.rel))
      .map((m) => `${m.rel}:${m.line} « ${m.key} » -> ${locale}/${m.zone}.json`);
    report(missing, `traductions manquantes (${locale}) ; liste à compléter : node test/i18n-scan.mjs --missing <zone>`);
  }
});

test('aucune entrée inutilisée dans les catalogues', () => {
  const unused = [];
  for (const locale of catalogLocales()) {
    for (const [zone, data] of Object.entries(catalogs[locale])) {
      for (const key of Object.keys(data)) if (!used.has(key)) unused.push(`${locale}/${zone}.json : « ${key} »`);
    }
  }
  report(unused, 'entrées que plus aucun appel n\'utilise (à retirer, ou appel à corriger)');
});

test('mêmes paramètres {x} des deux côtés, pluriels complets, texte simple ou pluriel selon l\'appel', () => {
  const problems = [];
  for (const locale of catalogLocales()) {
    const categories = new Intl.PluralRules(locale).resolvedOptions().pluralCategories;
    for (const [zone, data] of Object.entries(catalogs[locale])) {
      for (const [key, value] of Object.entries(data)) {
        const where = `${locale}/${zone}.json « ${key} »`;
        const uses = used.get(key) || [];
        const plural = uses.find((u) => u.fn === 'tn');
        const simple = uses.find((u) => u.fn !== 'tn');
        if (plural && simple) problems.push(`${where} : utilisée par tn() et par t() (${plural.rel}, ${simple.rel})`);
        const source = simple?.fn === 'pt' ? simple.text : key;
        if (typeof value === 'string') {
          if (!value.trim()) problems.push(`${where} : traduction vide`);
          if (plural) problems.push(`${where} : tn() attend { ${categories.join(', ')} }`);
          else if (!sameSet(placeholders(value), placeholders(source))) {
            problems.push(`${where} : paramètres {${[...placeholders(value)].join('}, {')}} au lieu de {${[...placeholders(source)].join('}, {')}}`);
          }
        } else if (value && typeof value === 'object' && !Array.isArray(value)) {
          if (simple) problems.push(`${where} : t() attend un texte, pas des formes de pluriel`);
          const forms = Object.keys(value);
          for (const c of categories) if (typeof value[c] !== 'string' || !value[c].trim()) problems.push(`${where} : forme « ${c} » manquante`);
          for (const c of forms) if (!categories.includes(c)) problems.push(`${where} : forme « ${c} » inconnue en ${locale}`);
          if (plural) {
            const french = new Set([...placeholders(key), ...placeholders(plural.plural)]);
            const all = new Set(forms.flatMap((c) => [...placeholders(value[c])]));
            for (const c of forms) for (const p of placeholders(value[c])) if (!french.has(p)) problems.push(`${where} : {${p}} absent du texte français`);
            if (!sameSet(all, french)) problems.push(`${where} : paramètres {${[...all].join('}, {')}} au lieu de {${[...french].join('}, {')}}`);
          }
        } else {
          problems.push(`${where} : texte ou { one, other… } attendu`);
        }
      }
    }
  }
  report(problems, 'entrées des catalogues');
});

test('une même clé dans deux zones : même traduction (sinon pt() avec un contexte)', () => {
  const problems = [];
  for (const locale of catalogLocales()) {
    const seen = new Map();
    for (const [zone, data] of Object.entries(catalogs[locale])) {
      for (const [key, value] of Object.entries(data)) {
        const previous = seen.get(key);
        if (previous && JSON.stringify(previous.value) !== JSON.stringify(value)) {
          problems.push(`${locale} « ${key} » : ${previous.zone}.json et ${zone}.json diffèrent`);
        }
        if (!previous) seen.set(key, { zone, value });
      }
    }
  }
  report(problems, 'clés en double');
});
