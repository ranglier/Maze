// Natures de lien de la campagne (graph.relation_types), indexées par clé.
import { t } from './i18n/core.js';

// Libellé traduit à la lecture (nature inconnue de la campagne) ; les natures de la campagne sont des données.
const FALLBACK = { get label() { return t('Autre'); }, color: '#8a93a3', directed: false, hint: '' };

// Palette proposée pour les nouvelles natures (reprend les couleurs par défaut).
export const RELATION_PALETTE = [
  '#5fb49c', '#d2555a', '#e0a33b', '#d77ba6', '#e07b39',
  '#6fa8dc', '#9c88d8', '#b8b25a', '#a8825e', '#8a93a3',
  '#4fc1d6', '#c94f8a',
];

export function typeIndex(graph) {
  return new Map((graph?.relation_types || []).map((type) => [type.key, type]));
}

export function typeOf(index, key) {
  return index.get(key) || { ...FALLBACK, key, label: key || FALLBACK.label };   // copie faite à l'affichage
}
