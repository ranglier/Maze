/*
 * Modèles de fiches fournis avec Maze. Les textes en italique sont des consignes à remplacer.
 * Les modèles enregistrés par les MJ viennent de l'API (/api/templates).
 *
 * Le contenu est composé dans la langue de l'interface au moment où il est demandé (builtinTemplates(), appelé à
 * l'affichage de la fenêtre de création) : une nouvelle fiche reçoit le modèle dans la langue courante. Les fiches
 * déjà créées ne sont jamais traduites.
 */
import { N_, pt, t } from '../i18n/core.js';

const esc = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const h2 = (text) => `<h2>${esc(text)}</h2>`;
const hint = (text) => `<p><em>${esc(text)}</em></p>`;
const table = (heads, rows = 2) => {
  const cells = (tag) => heads.map(() => `<${tag}><p></p></${tag}>`).join('');
  return `<table><tbody><tr>${heads.map((h) => `<th><p>${esc(h)}</p></th>`).join('')}</tr>${
    `<tr>${cells('td')}</tr>`.repeat(rows)}</tbody></table>`;
};
const callout = (variant, inner) => `<div class="callout" data-callout="${variant}">${inner}</div>`;
const emptyItems = (n) => '<li><p></p></li>'.repeat(n);
/** Élément de liste qui commence par une étiquette en gras (« On voit : »), à compléter. */
const lead = (text) => `<li><p><strong>${esc(text)}</strong> </p></li>`;

const TEMPLATES = [
  {
    key: 'scenario',
    name: N_('Scénario'),
    level: 'scenario',
    description: N_('Accroche, vérité cachée, PNJ impliqués, scènes, récompenses et suites.'),
    content: () => [
      h2(t('En bref')),
      hint(t('Ce qui se passe, en deux phrases, et ce que les PJ peuvent y gagner.')),
      callout('secret', hint(t('La vérité derrière l’intrigue : qui tire les ficelles, et pourquoi.'))),
      h2(pt('modele', 'Accroche')),
      hint(t('Comment les PJ sont entraînés dans l’histoire. Prévoir une seconde accroche si la première tombe à plat.')),
      h2(t('Contexte')),
      hint(t('Ce qui s’est passé avant, ce que les gens du coin en savent.')),
      h2(t('PNJ impliqués')),
      table([t('PNJ'), t('Rôle'), t('Ce qu’il veut'), t('Ce qu’il sait')]),
      h2(t('Scènes')),
      `<ol><li><p><em>${esc(t('Une scène par ligne ; détaillez-les dans des sous-pages (modèle « Scène »).'))}</em></p></li>${
        emptyItems(2)}</ol>`,
      h2(t('Récompenses')),
      `<ul><li><p><em>${esc(t('Trésor, informations, alliés, réputation…'))}</em></p></li></ul>`,
      h2(t('Suites possibles')),
      hint(t('Les conséquences selon ce que les PJ auront fait, les fils à reprendre plus tard.')),
    ].join(''),
  },
  {
    key: 'scene',
    name: N_('Scène'),
    level: 'page',
    description: N_('Situation, texte à lire, enjeux, opposition, indices et issues.'),
    content: () => [
      h2(t('Situation')),
      hint(t('Où, quand, qui est présent ; ce qui vient de se passer.')),
      callout('read', hint(t('Ce que les PJ voient et entendent en arrivant.'))),
      h2(t('Enjeux')),
      hint(t('Ce que les PJ peuvent gagner ou perdre ici.')),
      h2(t('Déroulement')),
      `<ul>${emptyItems(1)}</ul>`,
      h2(t('Opposition')),
      table([t('Adversaire'), t('Nombre'), t('Tactique, points faibles')], 1),
      h2(t('Indices et découvertes')),
      `<ul>${emptyItems(1)}</ul>`,
      h2(t('Issues')),
      `<ul>${lead(t('Si les PJ réussissent :'))}${lead(t('S’ils échouent :'))}</ul>`,
    ].join(''),
  },
  {
    key: 'place',
    name: N_('Lieu'),
    level: 'page',
    description: N_('Description à lire, ambiance, zones, habitants et secrets.'),
    content: () => [
      callout('read', hint(t('Première impression des PJ.'))),
      h2(t('Ambiance')),
      `<ul>${lead(t('On voit :'))}${lead(t('On entend :'))}${lead(t('On sent :'))}</ul>`,
      h2(t('Zones')),
      table([t('Zone'), t('Description'), t('Ce qu’on y trouve')], 3),
      h2(t('Habitants')),
      hint(t('Qui vit ou passe ici, et comment ils réagissent à des étrangers.')),
      callout('secret', hint(t('Ce que le lieu cache.'))),
    ].join(''),
  },
  {
    key: 'session',
    name: N_('Préparation de séance'),
    level: 'any',
    description: N_('Récapitulatif, ouverture, scènes possibles, secrets à placer, notes d’après-séance.'),
    content: () => [
      h2(t('Récapitulatif')),
      hint(t('Où en sont les PJ, ce qui s’est passé la dernière fois.')),
      h2(t('Scène d’ouverture')),
      hint(t('Une ouverture forte, qui lance la séance tout de suite.')),
      h2(t('Scènes possibles')),
      `<ul>${emptyItems(2)}</ul>`,
      h2(t('Secrets et indices à placer')),
      `<ul>${emptyItems(2)}</ul>`,
      h2(t('PNJ et lieux utiles')),
      hint(t('Tapez @ pour citer les fiches dont vous aurez besoin.')),
      h2(t('Après la séance')),
      hint(t('Ce qui a été joué, fils ouverts, récompenses distribuées.')),
    ].join(''),
  },
];

/** Modèles fournis, dans la langue courante de l'interface : { key, name, level, description, content (HTML) }. */
export function builtinTemplates() {
  return TEMPLATES.map((tpl) => ({ ...tpl, name: t(tpl.name), description: t(tpl.description), content: tpl.content() }));
}
