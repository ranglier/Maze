/*
 * Modèles de fiches fournis avec Maze. Les textes en italique sont des consignes à remplacer.
 * Les modèles enregistrés par les MJ viennent de l'API (/api/templates).
 */

const hint = (text) => `<p><em>${text}</em></p>`;
const table = (heads, rows = 2) => {
  const cells = (tag) => heads.map(() => `<${tag}><p></p></${tag}>`).join('');
  return `<table><tbody><tr>${heads.map((h) => `<th><p>${h}</p></th>`).join('')}</tr>${
    `<tr>${cells('td')}</tr>`.repeat(rows)}</tbody></table>`;
};
const callout = (variant, inner) => `<div class="callout" data-callout="${variant}">${inner}</div>`;

export const BUILTIN_TEMPLATES = [
  {
    key: 'scenario',
    name: 'Scénario',
    level: 'scenario',
    description: 'Accroche, vérité cachée, PNJ impliqués, scènes, récompenses et suites.',
    content: [
      '<h2>En bref</h2>',
      hint('Ce qui se passe, en deux phrases, et ce que les PJ peuvent y gagner.'),
      callout('secret', hint('La vérité derrière l’intrigue : qui tire les ficelles, et pourquoi.')),
      '<h2>Accroche</h2>',
      hint('Comment les PJ sont entraînés dans l’histoire. Prévoir une seconde accroche si la première tombe à plat.'),
      '<h2>Contexte</h2>',
      hint('Ce qui s’est passé avant, ce que les gens du coin en savent.'),
      '<h2>PNJ impliqués</h2>',
      table(['PNJ', 'Rôle', 'Ce qu’il veut', 'Ce qu’il sait']),
      '<h2>Scènes</h2>',
      '<ol><li><p><em>Une scène par ligne ; détaillez-les dans des sous-pages (modèle « Scène »).</em></p></li><li><p></p></li><li><p></p></li></ol>',
      '<h2>Récompenses</h2>',
      '<ul><li><p><em>Trésor, informations, alliés, réputation…</em></p></li></ul>',
      '<h2>Suites possibles</h2>',
      hint('Les conséquences selon ce que les PJ auront fait, les fils à reprendre plus tard.'),
    ].join(''),
  },
  {
    key: 'scene',
    name: 'Scène',
    level: 'page',
    description: 'Situation, texte à lire, enjeux, opposition, indices et issues.',
    content: [
      '<h2>Situation</h2>',
      hint('Où, quand, qui est présent ; ce qui vient de se passer.'),
      callout('read', hint('Ce que les PJ voient et entendent en arrivant.')),
      '<h2>Enjeux</h2>',
      hint('Ce que les PJ peuvent gagner ou perdre ici.'),
      '<h2>Déroulement</h2>',
      '<ul><li><p></p></li></ul>',
      '<h2>Opposition</h2>',
      table(['Adversaire', 'Nombre', 'Tactique, points faibles'], 1),
      '<h2>Indices et découvertes</h2>',
      '<ul><li><p></p></li></ul>',
      '<h2>Issues</h2>',
      '<ul><li><p><strong>Si les PJ réussissent :</strong> </p></li><li><p><strong>S’ils échouent :</strong> </p></li></ul>',
    ].join(''),
  },
  {
    key: 'place',
    name: 'Lieu',
    level: 'page',
    description: 'Description à lire, ambiance, zones, habitants et secrets.',
    content: [
      callout('read', hint('Première impression des PJ.')),
      '<h2>Ambiance</h2>',
      '<ul><li><p><strong>On voit :</strong> </p></li><li><p><strong>On entend :</strong> </p></li><li><p><strong>On sent :</strong> </p></li></ul>',
      '<h2>Zones</h2>',
      table(['Zone', 'Description', 'Ce qu’on y trouve'], 3),
      '<h2>Habitants</h2>',
      hint('Qui vit ou passe ici, et comment ils réagissent à des étrangers.'),
      callout('secret', hint('Ce que le lieu cache.')),
    ].join(''),
  },
  {
    key: 'session',
    name: 'Préparation de séance',
    level: 'any',
    description: 'Récapitulatif, ouverture, scènes possibles, secrets à placer, notes d’après-séance.',
    content: [
      '<h2>Récapitulatif</h2>',
      hint('Où en sont les PJ, ce qui s’est passé la dernière fois.'),
      '<h2>Scène d’ouverture</h2>',
      hint('Une ouverture forte, qui lance la séance tout de suite.'),
      '<h2>Scènes possibles</h2>',
      '<ul><li><p></p></li><li><p></p></li></ul>',
      '<h2>Secrets et indices à placer</h2>',
      '<ul><li><p></p></li><li><p></p></li></ul>',
      '<h2>PNJ et lieux utiles</h2>',
      hint('Tapez @ pour citer les fiches dont vous aurez besoin.'),
      '<h2>Après la séance</h2>',
      hint('Ce qui a été joué, fils ouverts, récompenses distribuées.'),
    ].join(''),
  },
];
