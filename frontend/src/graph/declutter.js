/*
 * Finition de la disposition automatique : fcose place les fiches, mais ignore les étiquettes des
 * liens. Ici, on écarte les fiches juste assez pour que :
 *   - chaque lien soit assez long pour afficher son étiquette entre les deux fiches ;
 *   - les étiquettes de liens ne se chevauchent pas, ni entre elles ni avec une fiche (et son nom) ;
 *   - aucun lien ne traverse une fiche qui ne le concerne pas, ni l'étiquette d'un autre lien.
 * Calcul pur (pas de DOM) : modèle en entrée, positions en sortie.
 *
 * Modèle :
 *   nodes : [{ id, x, y, w, h, labelW, labelH, labelGap }]  (nom affiché sous la fiche)
 *   edges : [{ id, s, t, labelW, labelH, offset }]          (offset : décalage des liens parallèles)
 */

const PAD = 6;            // marge autour des étiquettes
const LINK_GAP = 22;      // espace minimal entre une étiquette et chaque extrémité du lien
const MAX_STEP = 18;      // déplacement maximal d'une fiche par itération
const ITERATIONS = 700;
const LINE_CLEAR = 5;      // un lien ne frôle pas l'étiquette d'un autre lien
const NODE_CLEAR = 10;     // ni une fiche ; et deux fiches gardent de l'air entre elles

const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y });
// Liens parallèles (même paire de fiches) : ils sont courbés de part et d'autre, leurs tracés ne
// passent pas sur l'étiquette de l'autre.
const samePair = (a, b) => (a.e.s === b.e.s && a.e.t === b.e.t) || (a.e.s === b.e.t && a.e.t === b.e.s);
const dot = (a, b) => a.x * b.x + a.y * b.y;

/** Rectangle orienté : centre, axes unitaires, demi-dimensions. */
function rect(cx, cy, ux, uy, hw, hh) {
  return { c: { x: cx, y: cy }, u: { x: ux, y: uy }, v: { x: -uy, y: ux }, hw, hh };
}

function project(r, axis) {
  const c = dot(r.c, axis);
  const e = r.hw * Math.abs(dot(r.u, axis)) + r.hh * Math.abs(dot(r.v, axis));
  return [c - e, c + e];
}

/** Séparation minimale entre deux rectangles orientés (SAT) : vecteur pour pousser b hors de a, ou null. */
export function overlap(a, b) {
  // Rejet rapide : trop loin pour se toucher, quelle que soit l'orientation.
  const reach = a.hw + a.hh + b.hw + b.hh;
  if (Math.abs(a.c.x - b.c.x) > reach || Math.abs(a.c.y - b.c.y) > reach) return null;
  let best = null;
  for (const axis of [a.u, a.v, b.u, b.v]) {
    const [a0, a1] = project(a, axis);
    const [b0, b1] = project(b, axis);
    const depth = Math.min(a1, b1) - Math.max(a0, b0);
    if (depth <= 0.5) return null;  // simple contact des marges : pas un recouvrement
    if (!best || depth < best.depth) best = { depth, axis };
  }
  const sign = dot(sub(b.c, a.c), best.axis) < 0 ? -1 : 1;
  return { x: best.axis.x * best.depth * sign, y: best.axis.y * best.depth * sign, depth: best.depth };
}

/** Le segment [p, q] traverse-t-il le rectangle orienté r ? Renvoie la profondeur (côté du centre), ou 0. */
function segmentDepth(p, q, r0, margin = 0) {
  const r = margin ? { ...r0, hw: r0.hw + margin, hh: r0.hh + margin } : r0;
  const reach = r.hw + r.hh;
  if (Math.max(p.x, q.x) < r.c.x - reach || Math.min(p.x, q.x) > r.c.x + reach
      || Math.max(p.y, q.y) < r.c.y - reach || Math.min(p.y, q.y) > r.c.y + reach) return 0;
  // Repère du rectangle.
  const toLocal = (pt) => {
    const d = sub(pt, r.c);
    return { x: dot(d, r.u), y: dot(d, r.v) };
  };
  const a = toLocal(p);
  const b = toLocal(q);
  // Découpage de Liang-Barsky sur [-hw, hw] x [-hh, hh].
  let t0 = 0;
  let t1 = 1;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  for (const [pp, qq] of [[-dx, a.x + r.hw], [dx, r.hw - a.x], [-dy, a.y + r.hh], [dy, r.hh - a.y]]) {
    if (pp === 0) {
      if (qq < 0) return 0;
    } else {
      const t = qq / pp;
      if (pp < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return 0;
    }
  }
  // Distance du centre du rectangle à la droite : il faut l'écarter de (demi-épaisseur - distance).
  const len = Math.hypot(dx, dy) || 1;
  const dist = Math.abs(a.x * dy - a.y * dx) / len;
  const halfThickness = Math.abs(r.hw * dy) / len + Math.abs(r.hh * dx) / len;
  return Math.max(0, halfThickness - dist) + 1;
}

/** Distance du point p à la sortie de la boîte (axes alignés) le long de la direction (dx, dy), ou 0. */
function rayExit(p, dx, dy, box) {
  let t0 = -Infinity;
  let t1 = Infinity;
  for (const [o, d, lo, hi] of [[p.x, dx, box.c.x - box.hw, box.c.x + box.hw], [p.y, dy, box.c.y - box.hh, box.c.y + box.hh]]) {
    if (Math.abs(d) < 1e-9) {
      if (o < lo || o > hi) return 0;
    } else {
      const a = (lo - o) / d;
      const b = (hi - o) / d;
      t0 = Math.max(t0, Math.min(a, b));
      t1 = Math.min(t1, Math.max(a, b));
    }
  }
  return t1 > Math.max(t0, 0) ? t1 : 0;
}

function geometry(model, pos) {
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  const radius = (n, ux, uy) => {
    // Distance du centre au bord de la fiche dans la direction (ux, uy) (ellipse inscrite).
    const a = n.w / 2;
    const b = n.h / 2;
    return (a * b) / Math.hypot(b * ux, a * uy || 1e-9);
  };
  const index = new Map(model.nodes.map((n, i) => [n.id, i]));
  const nodeBoxes = model.nodes.map((n) => {
    const p = pos.get(n.id);
    const boxes = [rect(p.x, p.y, 1, 0, n.w / 2 + PAD, n.h / 2 + PAD)];
    if (n.labelW) {
      boxes.push(rect(p.x, p.y + n.h / 2 + n.labelGap + n.labelH / 2, 1, 0, n.labelW / 2 + PAD, n.labelH / 2 + PAD / 2));
    }
    return { id: n.id, boxes };
  });
  const edges = model.edges.map((e) => {
    const s = pos.get(e.s);
    const t = pos.get(e.t);
    const ns = byId.get(e.s);
    const nt = byId.get(e.t);
    let dx = t.x - s.x;
    let dy = t.y - s.y;
    const len = Math.hypot(dx, dy) || 1;
    dx /= len;
    dy /= len;
    const rs = radius(ns, dx, dy);
    const rt = radius(nt, dx, dy);
    // Encombrement réel de chaque extrémité dans l'axe du lien, nom de la fiche compris.
    const boxesS = nodeBoxes[index.get(e.s)].boxes;
    const boxesT = nodeBoxes[index.get(e.t)].boxes;
    const es = Math.max(rs, boxesS[1] ? rayExit(s, dx, dy, boxesS[1]) : 0);
    const et = Math.max(rt, boxesT[1] ? rayExit(t, -dx, -dy, boxesT[1]) : 0);
    const start = { x: s.x + dx * rs, y: s.y + dy * rs };
    const end = { x: t.x - dx * rt, y: t.y - dy * rt };
    const mx = (start.x + end.x) / 2 - dy * e.offset;
    const my = (start.y + end.y) / 2 + dx * e.offset;
    // Le texte suit le lien mais reste lisible (jamais à l'envers) : seule l'orientation de l'axe compte.
    const label = e.labelW ? rect(mx, my, dx, dy, e.labelW / 2 + PAD, e.labelH / 2 + PAD / 2) : null;
    // Place libre de part et d'autre de l'étiquette (centre au milieu du tracé, entre les bords).
    const mid = rs + (len - rs - rt) / 2;
    const room = Math.min(mid - es, len - mid - et);
    return { e, start, end, len, rs, rt, dx, dy, label, room };
  });
  return { nodeBoxes, edges };
}

/** Problèmes de lisibilité restants (pour les tests et le réglage). */
export function issues(model, pos, details = null) {
  const { nodeBoxes, edges } = geometry(model, pos);
  const out = { labelLabel: 0, labelNode: 0, short: 0, lineNode: 0, lineLabel: 0 };
  const note = (kind, a, b) => details?.push(`${kind}: ${a} / ${b}`);
  for (let i = 0; i < edges.length; i++) {
    const a = edges[i];
    if (!a.label) continue;
    if (a.room < a.e.labelW / 2 + LINK_GAP - 2) { out.short++; note('short', a.e.id, Math.round(a.room)); }
    for (let j = i + 1; j < edges.length; j++) {
      if (edges[j].label && overlap(a.label, edges[j].label)) { out.labelLabel++; note('labelLabel', a.e.id, edges[j].e.id); }
    }
    for (const nb of nodeBoxes) {
      if (nb.boxes.some((b) => overlap(b, a.label))) { out.labelNode++; note('labelNode', a.e.id, nb.id); }
    }
    for (const other of edges) {
      if (other !== a && !samePair(other, a) && segmentDepth(other.start, other.end, a.label) > 1) { out.lineLabel++; note('lineLabel', other.e.id, a.e.id); }
    }
  }
  for (const ed of edges) {
    for (const nb of nodeBoxes) {
      if (nb.id === ed.e.s || nb.id === ed.e.t) continue;
      if (segmentDepth(ed.start, ed.end, nb.boxes[0]) > 1) out.lineNode++;
    }
  }
  return out;
}

/** Somme des recouvrements restants (en pixels) et fiches concernées : critère de la finition. */
function energy(model, pos) {
  const { nodeBoxes, edges } = geometry(model, pos);
  let total = 0;
  const involved = new Set();
  const groups = new Map();   // groupes à déplacer ensemble : les deux extrémités d'un lien en cause
  const blame = (depth, ...ids) => {
    total += depth;
    ids.forEach((id) => involved.add(id));
    for (let k = 0; k + 1 < ids.length; k += 2) {
      if (ids.length >= 2 && k + 1 < ids.length) groups.set(`${ids[k]}|${ids[k + 1]}`, [ids[k], ids[k + 1]]);
    }
  };
  const inflate = (r, m) => ({ ...r, hw: r.hw + m, hh: r.hh + m });
  for (let i = 0; i < edges.length; i++) {
    const a = edges[i];
    if (!a.label) continue;
    const lack = a.e.labelW / 2 + LINK_GAP - a.room;
    if (lack > 0) blame(lack * 2, a.e.s, a.e.t);
    for (let j = i + 1; j < edges.length; j++) {
      const o = edges[j].label && overlap(a.label, edges[j].label);
      if (o) blame(o.depth, a.e.s, a.e.t, edges[j].e.s, edges[j].e.t);
    }
    for (const nb of nodeBoxes) {
      for (const box of nb.boxes) {
        const o = overlap(box, a.label);
        if (o) blame(o.depth, nb.id, a.e.s, a.e.t);
      }
    }
    for (const other of edges) {
      if (other === a || samePair(other, a)) continue;
      const depth = segmentDepth(other.start, other.end, a.label, LINE_CLEAR);
      if (depth > 1) blame(depth * 0.5, a.e.s, a.e.t, other.e.s, other.e.t);
    }
  }
  for (const ed of edges) {
    for (const nb of nodeBoxes) {
      if (nb.id === ed.e.s || nb.id === ed.e.t) continue;
      const depth = segmentDepth(ed.start, ed.end, nb.boxes[0], NODE_CLEAR);
      if (depth > 1) blame(depth, nb.id, ed.e.s, ed.e.t);
    }
  }
  for (let i = 0; i < nodeBoxes.length; i++) {
    for (let j = i + 1; j < nodeBoxes.length; j++) {
      for (const a of nodeBoxes[i].boxes) {
        for (const b of nodeBoxes[j].boxes) {
          const o = overlap(inflate(a, NODE_CLEAR / 2), inflate(b, NODE_CLEAR / 2));
          if (o) blame(o.depth, nodeBoxes[i].id, nodeBoxes[j].id);
        }
      }
    }
  }
  return { total, involved, groups };
}

/**
 * Finition locale : quand les poussées se contrarient (une fiche coincée entre plusieurs liens),
 * on essaie de déplacer une fiche concernée dans 8 directions et on garde le meilleur essai.
 * Bornée en temps, pour les grandes cartes.
 */
function polish(model, pos, budgetMs = 250) {
  const deadline = Date.now() + budgetMs;
  let current = energy(model, pos);
  const dirs = [];
  for (let k = 0; k < 8; k++) dirs.push([Math.cos((k * Math.PI) / 4), Math.sin((k * Math.PI) / 4)]);
  for (let round = 0; round < 60 && current.total > 0.5 && Date.now() < deadline; round++) {
    let best = null;
    // Finition réservée aux derniers accrocs : au-delà, elle coûterait trop pour peu de gain.
    if (current.involved.size > 30) break;
    const candidates = [...[...current.involved].map((id) => [id]), ...current.groups.values()];
    for (const group of candidates) {
      if (Date.now() > deadline) break;
      const pts = group.map((id) => pos.get(id));
      for (const dist of [8, 16, 32, 64, 110]) {
        for (const [dx, dy] of dirs) {
          pts.forEach((p) => { p.x += dx * dist; p.y += dy * dist; });
          // À gain égal, le plus petit déplacement.
          const e = energy(model, pos).total + dist * group.length * 0.01;
          pts.forEach((p) => { p.x -= dx * dist; p.y -= dy * dist; });
          if (e < current.total - 0.5 && (!best || e < best.e)) best = { pts, x: dx * dist, y: dy * dist, e };
        }
        if (Date.now() > deadline) break;
      }
    }
    if (!best) break;
    best.pts.forEach((p) => { p.x += best.x; p.y += best.y; });
    current = energy(model, pos);
  }
}

/** Écarte les fiches jusqu'à ce que les étiquettes soient lisibles. Renvoie Map id -> {x, y}. */
export function declutter(model, initial, { iterations = ITERATIONS, budgetMs = 1500 } = {}) {
  const pos = new Map([...initial].map(([id, p]) => [id, { x: p.x, y: p.y }]));
  const ids = model.nodes.map((n) => n.id);
  const deadline = Date.now() + budgetMs;

  for (let it = 0; it < iterations && Date.now() < deadline; it++) {
    const force = new Map(ids.map((id) => [id, { x: 0, y: 0, n: 0 }]));
    const push = (id, x, y) => {
      const f = force.get(id);
      if (!f) return;
      f.x += x;
      f.y += y;
      f.n++;
    };
    // Pousse les deux extrémités d'un lien (le lien entier se déplace).
    const pushEdge = (ed, x, y) => {
      push(ed.e.s, x, y);
      push(ed.e.t, x, y);
    };

    const { nodeBoxes, edges } = geometry(model, pos);
    let worst = 0;

    // 1. Liens trop courts pour leur étiquette : on écarte les extrémités.
    for (const ed of edges) {
      if (!ed.label) continue;
      // Allonger de L déplace le milieu de L/2 : il faut deux fois le manque.
      const need = 2 * (ed.e.labelW / 2 + LINK_GAP - ed.room);
      if (need > 0) {
        worst = Math.max(worst, need);
        push(ed.e.s, -ed.dx * need / 2, -ed.dy * need / 2);
        push(ed.e.t, ed.dx * need / 2, ed.dy * need / 2);
      }
    }

    // 2. Étiquettes qui se chevauchent : les deux liens s'écartent.
    for (let i = 0; i < edges.length; i++) {
      const a = edges[i];
      if (!a.label) continue;
      for (let j = i + 1; j < edges.length; j++) {
        const b = edges[j];
        if (!b.label) continue;
        const o = overlap(a.label, b.label);
        if (!o) continue;
        worst = Math.max(worst, o.depth);
        pushEdge(a, -o.x / 2, -o.y / 2);
        pushEdge(b, o.x / 2, o.y / 2);
      }
    }

    // 3. Étiquette posée sur une fiche (ou son nom) : la fiche et le lien s'écartent.
    for (const ed of edges) {
      if (!ed.label) continue;
      for (const nb of nodeBoxes) {
        for (const box of nb.boxes) {
          const o = overlap(ed.label, box);
          if (!o) continue;
          worst = Math.max(worst, o.depth);
          const own = nb.id === ed.e.s || nb.id === ed.e.t;
          if (own) {
            // Son propre lien : il faut l'allonger (déjà traité en 1) ; on renforce un peu.
            const sign = nb.id === ed.e.s ? -1 : 1;
            push(nb.id, sign * ed.dx * o.depth, sign * ed.dy * o.depth);
          } else {
            // Fiche étrangère : on l'écarte de part et d'autre du lien (glisser le long du lien la
            // ferait buter sur ses extrémités et osciller).
            const axis = ed.label.v;
            const [a0, a1] = project(ed.label, axis);
            const [b0, b1] = project(box, axis);
            const depthV = Math.min(a1, b1) - Math.max(a0, b0);
            const sgn = dot(sub(box.c, ed.label.c), axis) < 0 ? -1 : 1;
            push(nb.id, axis.x * sgn * depthV * 0.6, axis.y * sgn * depthV * 0.6);
            pushEdge(ed, -axis.x * sgn * depthV * 0.3, -axis.y * sgn * depthV * 0.3);
          }
        }
      }
    }

    // 4. Lien qui traverse une fiche étrangère, ou l'étiquette d'un autre lien.
    for (const ed of edges) {
      const nx = -ed.dy;
      const ny = ed.dx;
      const side = (pt) => (((pt.x - ed.start.x) * nx + (pt.y - ed.start.y) * ny) < 0 ? -1 : 1);
      for (const nb of nodeBoxes) {
        if (nb.id === ed.e.s || nb.id === ed.e.t) continue;
        const depth = segmentDepth(ed.start, ed.end, nb.boxes[0], NODE_CLEAR);
        if (!depth) continue;
        worst = Math.max(worst, depth);
        const sgn = side(nb.boxes[0].c);
        push(nb.id, nx * sgn * depth * 0.6, ny * sgn * depth * 0.6);
        pushEdge(ed, -nx * sgn * depth * 0.2, -ny * sgn * depth * 0.2);
      }
      for (const other of edges) {
        if (other === ed || !other.label || samePair(other, ed)) continue;
        const depth = segmentDepth(ed.start, ed.end, other.label, LINE_CLEAR);
        if (depth <= 1) continue;
        worst = Math.max(worst, depth * 0.5);
        const sgn = side(other.label.c);
        // Moins prioritaire : un croisement de lien est acceptable, une étiquette barrée l'est moins.
        pushEdge(other, nx * sgn * depth * 0.25, ny * sgn * depth * 0.25);
      }
    }

    // 5. Fiches qui se recouvrent (corps et noms).
    for (let i = 0; i < nodeBoxes.length; i++) {
      for (let j = i + 1; j < nodeBoxes.length; j++) {
        for (const a of nodeBoxes[i].boxes) {
          for (const b of nodeBoxes[j].boxes) {
            const o = overlap({ ...a, hw: a.hw + NODE_CLEAR / 2, hh: a.hh + NODE_CLEAR / 2 },
              { ...b, hw: b.hw + NODE_CLEAR / 2, hh: b.hh + NODE_CLEAR / 2 });
            if (!o) continue;
            worst = Math.max(worst, o.depth);
            push(nodeBoxes[i].id, -o.x / 2, -o.y / 2);
            push(nodeBoxes[j].id, o.x / 2, o.y / 2);
          }
        }
      }
    }

    if (worst < 0.5) break;
    // Application, amortie et bornée (évite les oscillations entre contraintes contradictoires).
    const damping = it < iterations * 0.7 ? 0.6 : 0.35;
    for (const id of ids) {
      const f = force.get(id);
      if (!f.n) continue;
      let x = f.x * damping;
      let y = f.y * damping;
      const len = Math.hypot(x, y);
      if (len > MAX_STEP) {
        x = (x / len) * MAX_STEP;
        y = (y / len) * MAX_STEP;
      }
      const p = pos.get(id);
      p.x += x;
      p.y += y;
    }
  }
  polish(model, pos);
  return pos;
}

/** Les segments [a, b] et [c, d] se croisent-ils (hors extrémités communes) ? */
function crosses(a, b, c, d) {
  const orient = (p, q, r) => Math.sign((q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x));
  return orient(a, b, c) * orient(a, b, d) < 0 && orient(c, d, a) * orient(c, d, b) < 0;
}

/**
 * Note d'une disposition (plus bas = plus lisible), pour choisir entre plusieurs essais :
 * recouvrements restants, croisements de liens, liens presque confondus (angle faible entre deux
 * liens d'une même fiche), et étalement (une carte compacte se lit mieux).
 */
export function layoutScore(model, pos) {
  const { total } = energy(model, pos);
  const { edges } = geometry(model, pos);
  let crossings = 0;
  let sharp = 0;
  for (let i = 0; i < edges.length; i++) {
    for (let j = i + 1; j < edges.length; j++) {
      const a = edges[i];
      const b = edges[j];
      if (samePair(a, b)) continue;
      const shared = [a.e.s, a.e.t].find((id) => id === b.e.s || id === b.e.t);
      if (shared) {
        // Directions au départ de la fiche commune.
        const da = shared === a.e.s ? [a.dx, a.dy] : [-a.dx, -a.dy];
        const db = shared === b.e.s ? [b.dx, b.dy] : [-b.dx, -b.dy];
        if (da[0] * db[0] + da[1] * db[1] > Math.cos((15 * Math.PI) / 180)) sharp++;
      } else if (crosses(a.start, a.end, b.start, b.end)) {
        crossings++;
      }
    }
  }
  const xs = [...pos.values()].map((p) => p.x);
  const ys = [...pos.values()].map((p) => p.y);
  const area = (Math.max(...xs) - Math.min(...xs) + 200) * (Math.max(...ys) - Math.min(...ys) + 200);
  const perNode = Math.sqrt(area / Math.max(1, pos.size));
  return total * 4 + crossings * 30 + sharp * 40 + perNode * 0.3;
}
