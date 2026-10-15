// Description des fiches : sert à la fois à l'affichage et au formulaire d'édition.
// Champs, libellés, secrets, historique et commentaires : registre du serveur (backend/maze/entries.py, audit M23),
// recopié par « python -m maze.entries » dans entry-fields.generated.js (vérifié par tests.test_registry). Ici, seuls
// les compléments de l'interface : textes d'aide, exemples, hauteur des champs (vérifiés par fields.test.mjs).
import { ENTRY_REGISTRY } from './entry-fields.generated.js';

export { ENTRY_REGISTRY };

const UI = {
  npc: {
    title: { placeholder: 'Receleuse, capitaine de la garde…' },
    summary: { hint: 'Une phrase, affichée au survol sur la carte.' },
  },
  faction: {
    kind: { placeholder: 'Guilde, culte, maison noble…' },
    summary: { hint: 'Une phrase, affichée au survol sur la carte.' },
  },
  place: {
    ambiance: { hint: 'Texte à lire aux joueurs : ce que les PJ voient, entendent, sentent.', rows: 3 },
    description: { hint: 'Tapez @ pour citer une fiche ou un lieu, / pour insérer un bloc.', rows: 5 },
    secrets: { rows: 3 },
    notes: { rows: 4 },
  },
};
export const UI_EXTRAS = UI;

// Type de saisie de l'en-tête : texte, liste des statuts ou palette de couleurs.
const INPUT = { name: 'text', short: 'text', status: 'status', color: 'color' };

const registry = (kind) => ENTRY_REGISTRY[kind];
const extra = (kind, key) => UI[kind]?.[key] || {};

function identityField(kind, f) {
  return { key: f.key, label: f.label, type: INPUT[f.type] || 'text', ...(f.type === 'name' ? { required: true } : {}),
    ...extra(kind, f.key) };
}

function sectionField(kind, f) {
  return { key: f.key, label: f.label, ...extra(kind, f.key), ...(f.secret ? { secret: true } : {}) };
}

function entityFields(kind) {
  const fields = registry(kind);
  const summary = fields.find((f) => f.group === 'summary');
  return {
    identity: fields.filter((f) => f.group === 'identity').map((f) => identityField(kind, f)),
    summary: { key: summary.key, label: summary.label, ...extra(kind, summary.key) },
    sections: fields.filter((f) => f.group === 'section').map((f) => sectionField(kind, f)),
  };
}

export const ENTITY_FIELDS = { npc: entityFields('npc'), faction: entityFields('faction') };

export function editableKeys(kind) {
  const f = ENTITY_FIELDS[kind];
  return [...f.identity.map((x) => x.key), f.summary.key, ...f.sections.map((x) => x.key), 'tags'];
}

// Champs longs d'un lieu (fiche et formulaire).
export const PLACE_SECTIONS = registry('place').filter((f) => f.group === 'section').map((f) => sectionField('place', f));

/** Champs du formulaire d'un lieu (anciens champs exclus), dans l'ordre du registre. */
export const PLACE_EDITABLE = registry('place').filter((f) => f.group !== 'legacy').map((f) => f.key);

/** Libellés des champs d'une fiche (conflits d'enregistrement, historique). */
export function fieldLabels(kind) {
  return Object.fromEntries(registry(kind).map((f) => [f.key, f.label]));
}

/** Sections de texte d'une fiche (PNJ, faction ou lieu), résumé compris : commentaires et historique. */
export function textSections(kind) {
  const summary = { key: 'summary', label: 'Résumé' };
  return [summary, ...(kind === 'place' ? PLACE_SECTIONS : ENTITY_FIELDS[kind].sections)];
}

/** Champs d'en-tête suivis par l'historique, nom excepté (aperçu d'une version), anciens champs compris. */
export function historyIdentity(kind) {
  return registry(kind).filter((f) => f.history && (f.group === 'identity' || f.group === 'legacy') && f.type !== 'name')
    .map((f) => ({ key: f.key, label: f.label, type: f.type }));
}
