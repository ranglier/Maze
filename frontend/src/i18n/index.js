// Traduction de l'interface : point d'entrée des composants React (voir README.md du dossier).
// Tout vient de core.js (utilisable sans React, par les tests Node et les modules purs), plus useLocale().
// t, tn et pt acceptent ici des éléments React en paramètres : t('Lire {lien}', { lien: <a …/> }) donne un fragment.
import { createElement, Fragment, useSyncExternalStore } from 'react';
import * as core from './core.js';

export {
  LOCALES, N_, SOURCE_LOCALE, applyPreferences, browserLocale, compareText, dateFormat, formatDate, formatList,
  formatLocale, formatNumber, getLocale, interpolate, languageName, matchLocale, numberFormat, relativeTime,
  resolveLocale, setLocale, storeLocale, storedLocale, subscribe,
} from './core.js';

const node = (value) => (Array.isArray(value) ? createElement(Fragment, null, ...value) : value);

export const t = (text, params) => node(core.t(text, params));
export const pt = (context, text, params) => node(core.pt(context, text, params));
export const tn = (singular, plural, n, params) => node(core.tn(singular, plural, n, params));

/** Langue courante ; le composant est redessiné quand elle change (App l'appelle : toute l'interface suit). */
export function useLocale() {
  return useSyncExternalStore(core.subscribe, core.getLocale, core.getLocale);
}
