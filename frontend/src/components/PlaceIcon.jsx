import { createContext, useContext } from 'react';
import { PLACE_KINDS, placeKind } from '../constants.js';
import { Icon, KindImage } from './ui.jsx';

// Types de lieux de la campagne ({ id, key, label, icon }), chargés avec l'arbre.
export const KindsContext = createContext(null);
const DEFAULT_KIND_LIST = Object.entries(PLACE_KINDS).map(([key, v]) => ({ key, ...v }));
export const useKinds = () => useContext(KindsContext) || DEFAULT_KIND_LIST;

export function PlaceIcon({ kind, size = 16 }) {
  const kinds = useContext(KindsContext);
  const k = placeKind(kind, kinds);
  if (k.image) return <KindImage src={k.image} size={size} title={k.label} />;
  return <Icon name={k.icon} size={size} title={k.label} />;
}
