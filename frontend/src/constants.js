// Vocabulaire partagé de l'interface.

export const NPC_STATUS = {
  vivant: { label: 'Vivant' },
  mort: { label: 'Mort' },
  disparu: { label: 'Disparu' },
  inconnu: { label: 'Sort inconnu' },
};

export const FACTION_COLORS = [
  '#c9a45c', '#b5524f', '#8e5aa8', '#4f7fb5', '#3f9c8a', '#6f9a43', '#b87a3d', '#7c8796',
];

export const NEUTRAL_RING = '#5d6b80';

export const KIND_LABEL = { npc: 'PNJ', faction: 'Faction', place: 'Lieu' };

// Types de lieux (clé enregistrée > libellé et icône), dans l'ordre des menus.
export const PLACE_KINDS = {
  region: { label: 'Région', icon: 'region' },
  ville: { label: 'Ville', icon: 'city' },
  quartier: { label: 'Quartier', icon: 'district' },
  batiment: { label: 'Bâtiment', icon: 'house' },
  salle: { label: 'Salle', icon: 'door' },
  donjon: { label: 'Donjon, souterrain', icon: 'cave' },
  nature: { label: 'Nature', icon: 'tree' },
  lieu: { label: 'Lieu', icon: 'pin' },
};
// Types de la campagne ({ key, label, icon, image_url } chargés avec les lieux) : ils priment sur les types
// fournis. image : icône importée (symbole de légende), qui remplace l'icône intégrée.
export const placeKind = (key, kinds) => {
  const found = kinds?.find((k) => k.key === key) || kinds?.find((k) => k.key === 'lieu');
  if (found) return { label: found.label, icon: found.icon, image: found.image_url || null };
  return PLACE_KINDS[key] || { label: key || 'Lieu', icon: 'pin' };
};

// Icônes proposées pour les types de lieux.
export const PLACE_ICONS = [
  'pin', 'region', 'mountain', 'island', 'water', 'tree', 'cave', 'city', 'district', 'house', 'door', 'castle',
  'tower', 'temple', 'tavern', 'shop', 'ruins', 'bridge', 'tent', 'signpost', 'ship', 'anchor', 'factory', 'planet',
  'globe', 'star', 'skull', 'crown', 'shield', 'flame', 'book', 'lock', 'eye',
];

// Rôles proposés pour relier un PNJ ou une faction à un lieu (texte libre accepté).
export const PLACE_ROLES = {
  npc: ['Vit ici', 'Travaille ici', 'Fréquente', 'Se cache ici', 'Dirige'],
  faction: ['Siège', 'Contrôle', 'Présente ici', 'Dispute', 'Possède'],
};

// Couleur des lieux (mentions, pastilles).
export const PLACE_COLOR = '#8aa36b';
// Renvois vers une autre fiche rédigée (mentions « doc: »).
export const DOC_COLOR = '#7f9cc4';
