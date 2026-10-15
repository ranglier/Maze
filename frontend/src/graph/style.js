// Feuille de style Cytoscape. Les couleurs reprennent les jetons de styles.css.
const INK = '#161d29';
const BONE = '#e8e2d4';
const BRASS = '#c9a45c';
const SERIF = '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif';
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const graphStyle = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      color: BONE,
      'font-family': SANS,
      'font-size': 13,
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 7,
      'text-wrap': 'ellipsis',
      'text-max-width': 140,
      'text-background-color': INK,
      'text-background-opacity': 0.82,
      'text-background-padding': 3,
      'text-background-shape': 'roundrectangle',
      'min-zoomed-font-size': 7,
      'overlay-opacity': 0,
      'transition-property': 'opacity, border-width',
      'transition-duration': 150,
    },
  },
  {
    selector: 'node.npc',
    style: {
      shape: 'ellipse',
      width: 60,
      height: 60,
      'background-color': '#2a3546',
      'background-image': 'data(image)',
      'background-fit': 'cover',
      'background-clip': 'node',
      'border-width': 3,
      'border-color': 'data(ring)',
    },
  },
  { selector: 'node.status-mort', style: { 'background-image-opacity': 0.4, 'border-style': 'double', 'border-width': 5, color: '#a7a194' } },
  { selector: 'node.status-disparu', style: { 'border-style': 'dashed', 'background-image-opacity': 0.7 } },
  { selector: 'node.status-inconnu', style: { 'border-style': 'dotted' } },
  {
    selector: 'node.faction',
    style: {
      shape: 'round-hexagon',
      width: 92,
      height: 84,
      'background-color': 'data(color)',
      'background-opacity': 0.92,
      'border-width': 2,
      'border-color': BONE,
      'border-opacity': 0.35,
      'font-family': SERIF,
      'font-size': 16,
      'font-weight': 'bold',
      'text-margin-y': 8,
      'text-max-width': 180,
    },
  },
  {
    selector: 'node.faction.has-emblem',
    style: {
      'background-image': 'data(image)',
      'background-fit': 'cover',
      'background-clip': 'node',
    },
  },
  {
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'control-point-step-size': 46,
      width: 2.5,
      'line-color': 'data(color)',
      'target-arrow-color': 'data(color)',
      'source-arrow-color': 'data(color)',
      label: 'data(label)',
      'font-family': SANS,
      'font-size': 10,
      color: BONE,
      'text-rotation': 'autorotate',
      'text-background-color': INK,
      'text-background-opacity': 0.88,
      'text-background-padding': 2,
      'text-background-shape': 'roundrectangle',
      // Vue d'ensemble : les natures de lien s'effacent (zoom < 1) ; elles restent visibles au survol
      // ou à la sélection d'une fiche (liens en surbrillance).
      'min-zoomed-font-size': 10,
      'overlay-opacity': 0,
      opacity: 0.9,
      'arrow-scale': 1.1,
    },
  },
  { selector: 'edge.directed', style: { 'target-arrow-shape': 'triangle' } },
  { selector: 'edge.secret', style: { 'line-style': 'dashed', 'line-dash-pattern': [7, 5] } },
  {
    selector: 'edge.membership',
    style: {
      width: 1.5,
      'line-style': 'dotted',
      opacity: 0.6,
      'font-size': 9,
      color: '#b9b2a3',
      'curve-style': 'straight',
    },
  },
  { selector: 'edge.membership.leader', style: { width: 2.5, opacity: 0.85 } },
  { selector: '.hidden', style: { display: 'none' } },
  // Filtre sur une fiche rédigée : voisins directs des fiches citées, en retrait.
  { selector: 'node.context', style: { opacity: 0.38 } },
  { selector: 'edge.context', style: { opacity: 0.25 } },
  { selector: '.faded', style: { opacity: 0.12 } },
  { selector: 'edge.faded', style: { opacity: 0.06, label: '' } },
  { selector: 'edge.highlight', style: { opacity: 1, width: 3.5, 'z-index': 10, 'font-size': 11, 'min-zoomed-font-size': 0 } },
  { selector: 'edge.membership.highlight', style: { width: 2.5 } },
  {
    selector: 'node:selected',
    style: { 'border-color': BRASS, 'border-width': 5, 'border-opacity': 1 },
  },
  { selector: 'edge:selected', style: { width: 5, opacity: 1, 'z-index': 20, 'min-zoomed-font-size': 0 } },
  {
    selector: 'node.link-source',
    style: { 'border-color': BRASS, 'border-width': 6, 'border-style': 'solid', 'border-opacity': 1 },
  },
];
