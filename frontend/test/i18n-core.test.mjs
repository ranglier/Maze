// Fonctions de traduction de l'interface (src/i18n/core.js) : repli sur le français, paramètres, pluriels,
// contexte, choix de la langue (compte, écran de connexion, navigateur, instance), formats.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeWindow } from './helpers.mjs';

fakeWindow();
globalThis.localStorage = window.localStorage;
const { CATALOGS } = await import('../src/i18n/catalogs.js');
const I = await import('../src/i18n/core.js');

const withLocale = (locale, fn) => {
  const before = I.getLocale();
  I.setLocale(locale);
  try { return fn(); } finally { I.setLocale(before); }
};
/** Entrées de catalogue ajoutées le temps d'un test. */
const withEntries = (entries, fn) => {
  Object.assign(CATALOGS.en, entries);
  try { return fn(); } finally { for (const k of Object.keys(entries)) delete CATALOGS.en[k]; }
};

test('langues : le français (source), puis un dossier par langue', () => {
  assert.equal(I.SOURCE_LOCALE, 'fr');
  assert.equal(I.LOCALES[0], 'fr');
  assert.ok(I.LOCALES.includes('en'));
  assert.equal(I.languageName('fr'), 'Français');
  assert.equal(I.languageName('en'), 'English');
});

test('t : catalogue de la langue, sinon texte français ; paramètres remplis', () => {
  withLocale('fr', () => {
    assert.equal(I.t('Annuler'), 'Annuler');
    assert.equal(I.t('Retirer {tag}', { tag: 'Port' }), 'Retirer Port');
  });
  withLocale('en', () => {
    assert.equal(I.t('Annuler'), 'Cancel');
    assert.equal(I.t('Retirer {tag}', { tag: 'Port' }), 'Remove Port');
    assert.equal(I.t('Phrase absente du catalogue {x}', { x: 2 }), 'Phrase absente du catalogue 2');
    assert.equal(I.t('Sans paramètre {x}'), 'Sans paramètre {x}');
    // Traduction vide : repli sur le français.
    withEntries({ 'Texte vide': '' }, () => assert.equal(I.t('Texte vide'), 'Texte vide'));
  });
});

test('paramètre élément (React) : morceaux de texte et éléments, dans l\'ordre de la traduction', () => {
  const link = { type: 'a' };
  withLocale('en', () => withEntries({ 'Voir {lien} ici': 'See here: {lien}' }, () => {
    assert.deepEqual(I.t('Voir {lien} ici', { lien: link }), ['See here: ', link]);
  }));
  withLocale('fr', () => assert.deepEqual(I.t('Voir {lien} ici', { lien: link }), ['Voir ', link, ' ici']));
});

test('pt : contexte, clé « contexte|texte »', () => {
  withLocale('en', () => {
    assert.equal(I.pt('nav', 'Carte'), 'Graph');
    assert.equal(I.pt('autre', 'Carte'), 'Carte');
  });
  withLocale('fr', () => assert.equal(I.pt('nav', 'Carte'), 'Carte'));
});

test('tn : règles de pluriel de la langue (Intl.PluralRules), français par défaut', () => {
  withLocale('fr', () => {
    assert.equal(I.tn('{n} lieu', '{n} lieux', 0), '0 lieu');
    assert.equal(I.tn('{n} lieu', '{n} lieux', 1), '1 lieu');
    assert.equal(I.tn('{n} lieu', '{n} lieux', 2), '2 lieux');
    assert.equal(I.tn('{n} lieu dans {zone}', '{n} lieux dans {zone}', 3, { zone: 'Ys' }), '3 lieux dans Ys');
  });
  withLocale('en', () => {
    assert.equal(I.tn('{n} lieu', '{n} lieux', 2), '2 lieux');   // pas dans le catalogue : français
    withEntries({ '{n} lieu': { one: '{n} place', other: '{n} places' } }, () => {
      assert.equal(I.tn('{n} lieu', '{n} lieux', 0), '0 places');
      assert.equal(I.tn('{n} lieu', '{n} lieux', 1), '1 place');
      assert.equal(I.tn('{n} lieu', '{n} lieux', 7), '7 places');
    });
    // Entrée qui n'est pas un pluriel : français.
    withEntries({ '{n} lieu': '{n} place(s)' }, () => assert.equal(I.tn('{n} lieu', '{n} lieux', 7), '7 lieux'));
  });
});

test('N_ : renvoie le texte tel quel (marque pour l\'extraction)', () => {
  assert.equal(I.N_('Liste des lieux'), 'Liste des lieux');
});

test('choix de la langue : compte, choix de ce navigateur, navigateur, instance, français', () => {
  assert.equal(I.resolveLocale({ account: 'en', stored: 'fr', browser: ['fr-FR'], fallback: 'fr' }), 'en');
  assert.equal(I.resolveLocale({ account: '', stored: 'en', browser: ['fr-FR'], fallback: 'fr' }), 'en');
  assert.equal(I.resolveLocale({ account: '', stored: '', browser: ['en-GB', 'fr'], fallback: 'fr' }), 'en');
  assert.equal(I.resolveLocale({ account: '', stored: '', browser: ['de-DE', 'en-US'], fallback: 'fr' }), 'en');
  assert.equal(I.resolveLocale({ account: '', stored: '', browser: ['de-DE'], fallback: 'en' }), 'en');
  assert.equal(I.resolveLocale({ account: '', stored: '', browser: ['de-DE'], fallback: 'fr' }), 'fr');
  assert.equal(I.resolveLocale({ account: 'de', stored: '', browser: [], fallback: 'xx' }), 'fr');
  assert.equal(I.matchLocale('en_US'), 'en');
  assert.equal(I.matchLocale('EN'), 'en');
  assert.equal(I.matchLocale('de'), null);
});

test('applyPreferences : préférences retenues, choix mémorisé dans le navigateur, <html lang>', () => {
  globalThis.document = { documentElement: { lang: 'fr' } };   // page simulée : langues du navigateur lues
  const nav = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { value: { languages: ['de-DE'] }, configurable: true });
  try {
    I.storeLocale('');
    I.applyPreferences({ account: 'en', fallback: 'fr' });
    assert.equal(I.getLocale(), 'en');
    assert.equal(document.documentElement.lang, 'en');
    I.applyPreferences({ account: '', fallback: 'fr' });
    assert.equal(I.getLocale(), 'fr');   // navigateur simulé sans langue
    I.storeLocale('en');
    assert.equal(I.storedLocale(), 'en');
    assert.equal(I.applyPreferences(), 'en');   // préférences précédentes, plus le choix mémorisé
    I.storeLocale('');
    assert.equal(I.applyPreferences(), 'fr');
    assert.equal(document.documentElement.lang, 'fr');
    globalThis.navigator.languages = ['en-GB', 'fr'];
    assert.equal(I.applyPreferences(), 'en');   // langue du navigateur
    assert.equal(I.formatLocale(), 'en-GB');     // formats : variante régionale du navigateur
  } finally {
    delete globalThis.document;
    if (nav) Object.defineProperty(globalThis, 'navigator', nav);
    else delete globalThis.navigator;
    I.setLocale('fr');
  }
});

test('stockage indisponible : choix ignoré sans erreur', () => {
  const saved = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw new Error('bloqué'); }, setItem() { throw new Error('bloqué'); },
    removeItem() { throw new Error('bloqué'); } };
  try {
    I.storeLocale('en');
    assert.equal(I.storedLocale(), '');
  } finally {
    globalThis.localStorage = saved;
  }
});

test('abonnés prévenus d\'un changement de langue (useLocale)', () => {
  const seen = [];
  const stop = I.subscribe((l) => seen.push(l));
  I.setLocale('en');
  I.setLocale('en');
  I.setLocale('fr');
  stop();
  I.setLocale('en');
  I.setLocale('fr');
  assert.deepEqual(seen, ['en', 'fr']);
});

test('formats : fr-FR en français comme avant, langue courante sinon', () => {
  const date = new Date(Date.UTC(2026, 9, 16, 12, 0));
  withLocale('fr', () => {
    assert.equal(I.formatLocale(), 'fr-FR');
    assert.equal(I.formatDate(date, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }),
      new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date));
    assert.equal(I.formatNumber(1234.5), new Intl.NumberFormat('fr-FR').format(1234.5));
    assert.equal(I.formatList(['A', 'B', 'C']), 'A, B et C');
    assert.ok(I.compareText('é', 'f') < 0);
  });
  withLocale('en', () => {
    assert.equal(I.formatDate(date, { month: 'long', timeZone: 'UTC' }), 'October');
    assert.equal(I.formatList(['A', 'B', 'C']), 'A, B, and C');
    assert.equal(I.relativeTime(-5, 'minute'), '5 minutes ago');
    assert.equal(I.formatDate('pas une date'), '');
  });
  // Formateur gardé en cache par langue et options.
  assert.equal(I.dateFormat({ month: 'long' }), I.dateFormat({ month: 'long' }));
});
