/*
 * Étiquettes des encadrés « Accroche » et « Secret » (.callout dans .doc-content : éditeur, mode lecture, aperçu
 * d'une version, champs des fiches). Elles sont dessinées par la feuille de style (::before), sans changer le schéma
 * de l'éditeur ni le HTML des fiches : la langue de l'interface y arrive par deux variables CSS posées sur <html>,
 * --callout-read-label et --callout-secret-label (texte CSS entre guillemets), mises à jour à chaque changement de
 * langue. styles.css les lit : content: var(--callout-read-label, "Accroche").
 */
import { subscribe, t } from '../i18n/core.js';

const cssString = (text) => `"${String(text).replace(/["\\]/g, '\\$&').replace(/\n/g, ' ')}"`;

export function applyCalloutLabels() {
  const root = globalThis.document?.documentElement;
  if (!root) return;
  root.style.setProperty('--callout-read-label', cssString(t('Accroche')));
  root.style.setProperty('--callout-secret-label', cssString(t('Secret')));
}

if (globalThis.document) {
  applyCalloutLabels();
  subscribe(applyCalloutLabels);
}
