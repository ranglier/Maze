import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { declutter, issues, layoutScore } from './declutter.js';
import { graphStyle } from './style.js';

cytoscape.use(fcose);

// Largeur affichée d'un texte (étiquettes des liens et noms des fiches).
const measureCanvas = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null;
const textWidth = (text, font) => {
  if (!text) return 0;
  if (!measureCanvas) return text.length * 6;
  measureCanvas.font = font;
  return measureCanvas.measureText(text).width;
};
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const SERIF = '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif';

const LAYOUT = {
  name: 'fcose',
  quality: 'proof',
  animate: false,
  fit: false,
  nodeDimensionsIncludeLabels: true,
  // Carte resserrée, lisible en vue d'ensemble : les noms des fiches priment ; les natures de lien
  // s'affichent en zoomant ou au survol, la disposition n'allonge plus les liens pour elles.
  nodeRepulsion: () => 8000,
  idealEdgeLength: (edge) => (edge.hasClass('membership') ? 70 : 105),
  edgeElasticity: (edge) => (edge.hasClass('membership') ? 0.6 : 0.4),
  nodeSeparation: 60,
  gravity: 0.3,
  numIter: 3000,
};

/** Modèle géométrique de la carte visible, pour la finition de la disposition (declutter.js). */
function buildModel(cy, visible) {
  const nodes = visible.nodes().map((n) => {
    const faction = n.hasClass('faction');
    const label = n.data('label') || '';
    const maxW = faction ? 180 : 140;
    const font = faction ? `bold 16px ${SERIF}` : `13px ${SANS}`;
    return {
      id: n.id(),
      w: faction ? 92 : 66,
      h: faction ? 84 : 66,
      labelW: Math.min(textWidth(label, font), maxW) + 6,
      labelH: faction ? 22 : 18,
      labelGap: faction ? 6 : 5,
    };
  });
  // Liens parallèles (même paire de fiches) : Cytoscape les courbe de part et d'autre.
  const pairs = new Map();
  const edgesList = visible.edges().filter((e) => !e.hasClass('hidden') && !e.source().hasClass('hidden') && !e.target().hasClass('hidden'));
  edgesList.forEach((e) => {
    const key = [e.data('source'), e.data('target')].sort().join('|');
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(e);
  });
  const edges = [];
  for (const group of pairs.values()) {
    group.forEach((e, i) => {
      const flipped = e.data('source') > e.data('target') ? -1 : 1;
      const offset = group.length > 1 && !e.hasClass('membership') ? ((i - (group.length - 1) / 2) * 46 / 2) * flipped : 0;
      edges.push({
        id: e.id(), s: e.data('source'), t: e.data('target'),
        // Étiquettes des liens hors calcul (masquées en vue d'ensemble) : seules comptent les fiches et leurs noms.
        labelW: 0, labelH: 0, offset,
      });
    });
  }
  return { nodes, edges };
}

/**
 * Étire la disposition aux proportions de l'écran : fcose produit une carte à peu près ronde, qui laisse
 * de grands vides sur un écran large. À surface égale (x × √k, y ÷ √k), la carte cadrée zoome davantage ;
 * la finition (declutter) rouvre ensuite l'espace là où des fiches se touchent.
 */
function fitAspect(pos, ratio) {
  if (pos.size < 3 || !Number.isFinite(ratio) || ratio <= 0) return pos;
  let x0 = Infinity; let x1 = -Infinity; let y0 = Infinity; let y1 = -Infinity;
  for (const p of pos.values()) {
    x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
  }
  const w = Math.max(x1 - x0, 1);
  const h = Math.max(y1 - y0, 1);
  const k = Math.min(2.5, Math.max(0.4, ratio / (w / h)));
  if (Math.abs(k - 1) < 0.1) return pos;
  const sx = Math.sqrt(k);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  return new Map([...pos].map(([id, p]) => [id, { x: cx + (p.x - cx) * sx, y: cy + (p.y - cy) / sx }]));
}

/** Cadre la carte (fiches visibles) avec une marge réduite, sans agrandir au-delà de 1,4. */
function fitView(cy, animate = false) {
  const eles = cy.elements().not('.hidden');
  if (eles.empty()) return;
  cy.maxZoom(1.4);
  if (animate) cy.animate({ fit: { eles, padding: 36 } }, { duration: 350 });
  else cy.fit(eles, 36);
  cy.maxZoom(3);
}

const positionMap = (nodes) => new Map(nodes.map((n) => [n.id(), { ...n.position() }]));

function positionsOf(nodes) {
  return nodes.map((n) => {
    const { x, y } = n.position();
    return { kind: n.data('kind'), id: n.data('eid'), x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
  });
}

const insets = (inset) => (typeof inset === 'number' ? { right: inset, bottom: 0 } : { right: inset?.right || 0, bottom: inset?.bottom || 0 });

export default function GraphView({
  ref, elements, selectedId, linkSourceId, describe, linkMode,
  onNodeTap, onEdgeTap, onBackgroundTap, onBackgroundDblTap, onPositions,
}) {
  const wrapRef = useRef(null);
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const firstSync = useRef(true);
  const handlers = useRef({});
  const [hover, setHover] = useState(null);

  handlers.current = { onNodeTap, onEdgeTap, onBackgroundTap, onBackgroundDblTap, onPositions, describe };

  // --- Création de l'instance ---------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    const cy = cytoscape({
      container,
      style: graphStyle,
      minZoom: 0.15,
      maxZoom: 3,
      boxSelectionEnabled: true,
      selectionType: 'single',
    });
    cyRef.current = cy;

    const syncGrid = () => {
      const zoom = cy.zoom();
      const pan = cy.pan();
      const wrap = wrapRef.current;
      wrap.style.setProperty('--grid-size', `${40 * zoom}px`);
      wrap.style.setProperty('--grid-x', `${pan.x}px`);
      wrap.style.setProperty('--grid-y', `${pan.y}px`);
    };
    cy.on('viewport', syncGrid);
    syncGrid();

    const focusOn = (ele) => {
      cy.batch(() => {
        cy.elements().removeClass('faded highlight');
        if (!ele) return;
        const hood = ele.closedNeighborhood();
        cy.elements().not(hood).addClass('faded');
        ele.connectedEdges().addClass('highlight');
      });
    };
    const refocusSelection = () => {
      const sel = cy.$('node:selected');
      focusOn(sel.length === 1 ? sel[0] : null);
    };

    cy.on('tap', 'node', (evt) => handlers.current.onNodeTap?.(evt.target.id()));
    cy.on('tap', 'edge', (evt) => handlers.current.onEdgeTap?.(evt.target.id()));
    cy.on('tap', (evt) => { if (evt.target === cy) handlers.current.onBackgroundTap?.(); });
    cy.on('dbltap', (evt) => {
      if (evt.target === cy) handlers.current.onBackgroundDblTap?.(evt.position);
    });

    cy.on('mouseover', 'node', (evt) => {
      if (evt.target.grabbed()) return;
      focusOn(evt.target);
      const pos = evt.target.renderedPosition();
      setHover({ id: evt.target.id(), x: pos.x, y: pos.y, h: evt.target.renderedOuterHeight() });
    });
    cy.on('mouseout', 'node', () => { setHover(null); refocusSelection(); });
    cy.on('grab', 'node', () => setHover(null));
    cy.on('select unselect', 'node', refocusSelection);
    cy.on('viewport', () => setHover(null));

    // Enregistrement des positions après déplacement (regroupé).
    let pending = new Set();
    let timer;
    cy.on('dragfree', 'node', (evt) => {
      pending.add(evt.target);
      clearTimeout(timer);
      timer = setTimeout(() => {
        const nodes = [...pending].filter((n) => !n.removed());
        pending = new Set();
        if (nodes.length) handlers.current.onPositions?.(positionsOf(nodes));
      }, 250);
    });

    // Le conteneur change de taille (panneau, fenêtre, barre d'outils sur mobile).
    let fitted = false;
    const observer = new ResizeObserver(() => {
      cy.resize();
      // Tant que l'utilisateur n'a pas bougé la vue, on garde la carte cadrée.
      if (!fitted && cy.nodes().length) fitView(cy);
    });
    observer.observe(container);
    const markInteracted = () => { fitted = true; };
    cy.one('tapstart dragpan scrollzoom pinchzoom', markInteracted);

    // Zoom à la molette géré ici : celui de Cytoscape, qui « devine » le type de molette, ne zoome que de
    // quelques pour cent par cran sur une souris. Ici : environ ×1,3 par cran, progressif sur un pavé tactile
    // (et pincement : Ctrl + molette, petits écarts), centré sur le pointeur.
    const onWheel = (e) => {
      e.preventDefault();
      e.stopPropagation();
      markInteracted();
      const delta = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
      const factor = Math.exp(-delta * (e.ctrlKey ? 0.01 : 0.0025));
      const level = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor));
      const rect = container.getBoundingClientRect();
      cy.zoom({ level, renderedPosition: { x: e.clientX - rect.left, y: e.clientY - rect.top } });
    };
    container.addEventListener('wheel', onWheel, { capture: true, passive: false });

    // Diagnostic (tests) : problèmes de lisibilité restants sur la carte affichée.
    container.mazeLabelIssues = () => {
      const visible = cy.elements().not('.hidden');
      return issues(buildModel(cy, visible), positionMap(visible.nodes()));
    };

    return () => {
      clearTimeout(timer); observer.disconnect(); container.removeEventListener('wheel', onWheel, { capture: true }); cy.destroy();
    };
  }, []);

  // --- Synchronisation des éléments ---------------------------------------
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const wanted = new Map(elements.map((e) => [e.data.id, e]));
    const unplaced = [];
    let anyPositioned = false;

    cy.batch(() => {
      cy.elements().forEach((ele) => { if (!wanted.has(ele.id())) ele.remove(); });
      const newEdges = [];
      for (const el of elements) {
        const existing = cy.getElementById(el.data.id);
        if (existing.nonempty()) {
          if (el.group === 'edges'
            && (existing.data('source') !== el.data.source || existing.data('target') !== el.data.target)) {
            existing.remove();
            newEdges.push(el);
            continue;
          }
          existing.data(el.data);
          existing.classes(el.classes);
          if (el.group === 'nodes') anyPositioned = true;
        } else if (el.group === 'nodes') {
          const added = cy.add(el);
          if (el.position) anyPositioned = true;
          else unplaced.push(added);
        } else {
          newEdges.push(el);
        }
      }
      cy.add(newEdges);
    });

    if (firstSync.current) {
      firstSync.current = false;
      if (cy.nodes().length && !anyPositioned) {
        runLayout();
        return;
      }
      fitView(cy);
    }

    if (unplaced.length) {
      const ext = cy.extent();
      const center = { x: (ext.x1 + ext.x2) / 2, y: (ext.y1 + ext.y2) / 2 };
      const unplacedIds = new Set(unplaced.map((n) => n.id()));
      for (const node of unplaced) {
        const anchors = node.neighborhood('node').filter((n) => !unplacedIds.has(n.id()));
        const base = anchors.length
          ? {
            x: anchors.reduce((s, n) => s + n.position('x'), 0) / anchors.length,
            y: anchors.reduce((s, n) => s + n.position('y'), 0) / anchors.length,
          }
          : center;
        const angle = Math.random() * Math.PI * 2;
        const dist = anchors.length ? 120 : Math.random() * 60;
        node.position({ x: base.x + Math.cos(angle) * dist, y: base.y + Math.sin(angle) * dist });
      }
      handlers.current.onPositions?.(positionsOf(unplaced));
    }
  }, [elements]);

  // --- Sélection et source de liaison -------------------------------------
  useEffect(() => {
    const cy = cyRef.current;
    cy.batch(() => {
      cy.$(':selected').unselect();
      if (selectedId) cy.getElementById(selectedId).select();
    });
  }, [selectedId, elements]);

  useEffect(() => {
    const cy = cyRef.current;
    cy.nodes('.link-source').removeClass('link-source');
    if (linkSourceId) cy.getElementById(linkSourceId).addClass('link-source');
  }, [linkSourceId, elements]);

  /**
   * Réorganisation : fcose calcule plusieurs dispositions (sans animation), les fiches de chacune
   * sont écartées pour que les étiquettes des liens restent lisibles, et la plus lisible est
   * retenue ; la carte glisse ensuite vers le résultat.
   */
  function runLayout() {
    const cy = cyRef.current;
    const visible = cy.elements().not('.hidden');
    const count = visible.nodes().length;
    if (!count) return;
    const before = positionMap(visible.nodes());
    const model = buildModel(cy, visible);
    // Le coût de fcose « proof » explose avec la taille (1000 fiches : près de 2 minutes, page figée) :
    // qualité et finition dégressives. Une carte de scénario reste dans la première tranche.
    const attempts = count <= 40 ? 3 : count <= 90 ? 2 : 1;
    const quality = count <= 150 ? 'proof' : count <= 600 ? 'default' : 'draft';
    const finish = count <= 200;
    let best = null;
    for (let i = 0; i < attempts; i++) {
      // fcose sans animation est synchrone : les positions sont lisibles juste après run().
      visible.layout({ ...LAYOUT, quality, numIter: count <= 150 ? LAYOUT.numIter : 1500, randomize: true }).run();
      const placed = fitAspect(positionMap(visible.nodes()), cy.width() / Math.max(1, cy.height()));
      const target = finish ? declutter(model, placed) : placed;
      const score = attempts > 1 ? layoutScore(model, target) : 0;
      if (!best || score < best.score) best = { target, score };
    }
    // Retour aux positions de départ (même passe de rendu), puis animation vers le résultat.
    cy.batch(() => visible.nodes().forEach((n) => n.position(before.get(n.id()))));
    const anim = visible.layout({
      name: 'preset', positions: (n) => best.target.get(n.id()), animate: true, animationDuration: 650,
      animationEasing: 'ease-in-out-cubic', fit: false,
    });
    anim.one('layoutstop', () => {
      fitView(cy, true);
      handlers.current.onPositions?.(positionsOf(visible.nodes()));
    });
    anim.run();
  }

  useImperativeHandle(ref, () => ({
    fit() {
      const cy = cyRef.current;
      fitView(cy, true);
    },
    // Centre l'élément dans la zone visible, hors panneau : `inset` = largeur à droite, ou { right, bottom }.
    focus(id, inset = 0) {
      const cy = cyRef.current;
      const ele = cy.getElementById(id);
      if (ele.empty() || ele.hasClass('hidden')) return false;
      const { right, bottom } = insets(inset);
      const zoom = Math.max(cy.zoom(), 1.1);
      const pos = ele.isNode() ? ele.position() : ele.midpoint();
      const visibleW = Math.max(cy.width() - right, cy.width() * 0.4);
      const visibleH = Math.max(cy.height() - bottom, cy.height() * 0.3);
      cy.animate({
        zoom,
        pan: { x: visibleW / 2 - pos.x * zoom, y: visibleH / 2 - pos.y * zoom },
      }, { duration: 400 });
      return true;
    },
    // Décale la vue si l'élément est caché sous le panneau, sans changer le zoom.
    reveal(id, inset = 0) {
      const cy = cyRef.current;
      const ele = cy.getElementById(id);
      if (ele.empty()) return;
      const { right, bottom } = insets(inset);
      const box = ele.renderedBoundingBox();
      const limitX = cy.width() - right - 24;
      const limitY = cy.height() - bottom - 24;
      const dx = box.x2 > limitX ? limitX - box.x2 - 40 : 0;
      // Sous une feuille basse : l'élément remonte au milieu de la partie visible.
      const dy = bottom && box.y2 > limitY ? (cy.height() - bottom) / 2 - (box.y1 + box.y2) / 2 : 0;
      if (dx || dy) cy.animate({ panBy: { x: dx, y: dy } }, { duration: 300 });
    },
    layout: runLayout,
    viewportCenter() {
      const ext = cyRef.current.extent();
      return { x: (ext.x1 + ext.x2) / 2, y: (ext.y1 + ext.y2) / 2 };
    },
    async exportPng(filename) {
      const blob = await cyRef.current.png({ output: 'blob-promise', full: true, scale: 2, bg: '#161d29' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    },
  }));

  const info = hover ? describe?.(hover.id) : null;

  return (
    <div className={`graph${linkMode ? ' graph--linking' : ''}`} ref={wrapRef}>
      <div className="graph__canvas" ref={containerRef} />
      {info && (
        <div
          className="graph-tooltip"
          style={{ left: hover.x, top: hover.y + hover.h / 2 + 30 }}
          role="tooltip"
        >
          <strong>{info.title}</strong>
          {info.subtitle && <span className="graph-tooltip__sub">{info.subtitle}</span>}
          {info.text && <p>{info.text}</p>}
        </div>
      )}
    </div>
  );
}
