// Fiches rédigées : statuts et ordre de l'arbre (fiche de campagne > scénarios > pages).
import { t } from '../../i18n/core.js';

// Valeurs enregistrées (brouillon, pret, en_cours, joue) inchangées ; libellé traduit à chaque lecture (getter).
export const DOC_STATUS = {
  brouillon: { get label() { return t('Brouillon'); } },
  pret: { get label() { return t('Prêt'); } },
  en_cours: { get label() { return t('En cours'); } },
  joue: { get label() { return t('Joué'); } },
};

export function childrenOf(docs, parentId) {
  return docs.filter((d) => d.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
}
