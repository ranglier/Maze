import { createContext, useContext } from 'react';
import { PLACE_KINDS, placeKind } from '../constants.js';
import { useLocale } from '../i18n/index.js';
import { Icon, KindImage } from './ui.jsx';

// Types de lieux de la campagne ({ id, key, label, icon }), chargés avec l'arbre.
export const KindsContext = createContext(null);
// Liste refaite quand la langue change (libellés de PLACE_KINDS traduits à la lecture), stable sinon (dépendances
// des useMemo qui la reçoivent).
const defaults = new Map();
const defaultKinds = (locale) => {
  if (!defaults.has(locale)) {
    defaults.set(locale, Object.entries(PLACE_KINDS).map(([key, v]) => ({ key, label: v.label, icon: v.icon })));
  }
  return defaults.get(locale);
};
export const useKinds = () => {
  const locale = useLocale();
  return useContext(KindsContext) || defaultKinds(locale);
};

export function PlaceIcon({ kind, size = 16 }) {
  const kinds = useContext(KindsContext);
  const k = placeKind(kind, kinds);
  if (k.image) return <KindImage src={k.image} size={size} title={k.label} />;
  return <Icon name={k.icon} size={size} title={k.label} />;
}
