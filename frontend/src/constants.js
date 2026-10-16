// Vocabulaire partagé de l'interface.
//
// Libellés traduits à l'affichage, pas à l'import (la langue peut changer) : propriétés calculées (get label()), lues
// au moment du rendu. Ne pas recopier ces objets au chargement d'un module ({ ...v } fige le texte de ce moment-là).
import { t } from './i18n/core.js';

export const NPC_STATUS = {
  vivant: { get label() { return t('Vivant'); } },
  mort: { get label() { return t('Mort'); } },
  disparu: { get label() { return t('Disparu'); } },
  inconnu: { get label() { return t('Sort inconnu'); } },
};

export const FACTION_COLORS = [
  '#c9a45c', '#b5524f', '#8e5aa8', '#4f7fb5', '#3f9c8a', '#6f9a43', '#b87a3d', '#7c8796',
];

export const NEUTRAL_RING = '#5d6b80';

export const KIND_LABEL = {
  get npc() { return t('PNJ'); },
  get faction() { return t('Faction'); },
  get place() { return t('Lieu'); },
};

// Types de lieux (clé enregistrée > libellé et icône), dans l'ordre des menus.
// Types fournis ; ceux d'une campagne sont des données (place_kinds, libellés saisis ou créés avec la campagne).
export const PLACE_KINDS = {
  region: { get label() { return t('Région'); }, icon: 'region' },
  ville: { get label() { return t('Ville'); }, icon: 'city' },
  quartier: { get label() { return t('Quartier'); }, icon: 'district' },
  batiment: { get label() { return t('Bâtiment'); }, icon: 'house' },
  salle: { get label() { return t('Salle'); }, icon: 'door' },
  donjon: { get label() { return t('Donjon, souterrain'); }, icon: 'cave' },
  nature: { get label() { return t('Nature'); }, icon: 'tree' },
  lieu: { get label() { return t('Lieu'); }, icon: 'pin' },
};
// Types de la campagne ({ key, label, icon, image_url } chargés avec les lieux) : ils priment sur les types
// fournis. image : icône importée (symbole de légende), qui remplace l'icône intégrée.
export const placeKind = (key, kinds) => {
  const found = kinds?.find((k) => k.key === key) || kinds?.find((k) => k.key === 'lieu');
  if (found) return { label: found.label, icon: found.icon, image: found.image_url || null };
  return PLACE_KINDS[key] || { label: key || t('Lieu'), icon: 'pin' };
};

// Icônes proposées pour les types de lieux.
export const PLACE_ICONS = [
  'pin', 'region', 'mountain', 'island', 'water', 'tree', 'cave', 'city', 'district', 'house', 'door', 'castle',
  'tower', 'temple', 'tavern', 'shop', 'ruins', 'bridge', 'tent', 'signpost', 'ship', 'anchor', 'factory', 'planet',
  'globe', 'star', 'skull', 'crown', 'shield', 'flame', 'book', 'lock', 'eye',
];

// Rôles proposés pour relier un PNJ ou une faction à un lieu (texte libre accepté, enregistré tel quel : les rôles
// déjà saisis restent dans leur langue).
export const PLACE_ROLES = {
  get npc() { return [t('Vit ici'), t('Travaille ici'), t('Fréquente'), t('Se cache ici'), t('Dirige')]; },
  get faction() { return [t('Siège'), t('Contrôle'), t('Présente ici'), t('Dispute'), t('Possède')]; },
};

// Couleur des lieux (mentions, pastilles).
export const PLACE_COLOR = '#8aa36b';
// Renvois vers une autre fiche rédigée (mentions « doc: »).
export const DOC_COLOR = '#7f9cc4';
