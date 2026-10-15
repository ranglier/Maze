// Natures de lien de la campagne (graph.relation_types), indexées par clé.

const FALLBACK = { label: 'Autre', color: '#8a93a3', directed: false, hint: '' };

// Palette proposée pour les nouvelles natures (reprend les couleurs par défaut).
export const RELATION_PALETTE = [
  '#5fb49c', '#d2555a', '#e0a33b', '#d77ba6', '#e07b39',
  '#6fa8dc', '#9c88d8', '#b8b25a', '#a8825e', '#8a93a3',
  '#4fc1d6', '#c94f8a',
];

export function typeIndex(graph) {
  return new Map((graph?.relation_types || []).map((t) => [t.key, t]));
}

export function typeOf(index, key) {
  return index.get(key) || { ...FALLBACK, key, label: key || FALLBACK.label };
}
