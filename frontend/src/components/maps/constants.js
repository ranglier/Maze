import { N_ } from '../../i18n/core.js';
import { isLandscapePhone, isPhone } from '../../viewport.js';

// Fiche du lieu à droite de la carte (grand écran, téléphone en paysage) ou en feuille basse (téléphone).
export const panelBeside = () => !isPhone() || isLandscapePhone();

// Téléphone : un lieu choisi dans le tiroir referme celui-ci pour montrer la carte.
export const closeDrawer = () => window.dispatchEvent(new CustomEvent('maze:drawer', { detail: { open: false } }));

export const DRAG_TYPE = 'application/x-maze-place';
// Libellés traduits à l'affichage : t(style.label), t(style.hint).
export const PIN_STYLES = [
  { key: 'dot', icon: 'pin', label: N_('Pastilles'), hint: N_('Une pastille par lieu épinglé') },
  { key: 'icon', icon: 'image', label: N_('Icônes du type'), hint: N_('L’image importée dans Types de lieux') },
  { key: 'hidden', icon: 'eye', label: N_('Discrètes'), hint: N_('Invisibles, posées sur les symboles de la carte') },
];
export const PREVIEW_SIDE = 4096;  // côté de la version d'affichage ; au-delà, en zoomant, la pleine définition
