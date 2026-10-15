// Anneaux des sous-lieux sur une carte interactive : disposition des puces autour d'une épingle (calcul pur,
// sans DOM ; testé par frontend/test/rings.test.mjs).
import { childrenOf, clamp } from './mapTree.js';

export const PAGE = 10;          // lieux par anneau (au-delà : bouton « +N » qui fait défiler)

// Largeur approximative d'une puce (icône, nom, compteur) : sert à espacer les puces d'un anneau.
export const chipWidth = (name, extra) => Math.min(190, 46 + name.length * 7.2) + extra;
export const CHIP_H = 36;   // hauteur d'une puce et marge

/** Rayon pour que des puces voisines (largeurs w, angles a) ne se chevauchent pas. */
export function ringRadius(angles, widths, closed, min) {
  let r = min;
  const n = angles.length;
  for (let i = 0; i < n - (closed ? 0 : 1); i++) {
    const j = (i + 1) % n;
    let delta = angles[j] - angles[i];
    if (closed && j === 0) delta += 2 * Math.PI;
    const mid = angles[i] + delta / 2;
    const tx = Math.abs(Math.sin(mid));
    const ty = Math.abs(Math.cos(mid));
    // Distance entre centres nécessaire dans la direction de la tangente.
    const need = Math.min(((widths[i] + widths[j]) / 2 + 8) / Math.max(tx, 1e-3), CHIP_H / Math.max(ty, 1e-3));
    r = Math.max(r, need / (2 * Math.sin(Math.min(Math.PI / 2, delta / 2))));
  }
  return r;
}

export const NO_EXPANDED = {};
const COLLAPSED_R = 58;   // anneau replié : seule la puce « +N autres » y reste
const LINK_GAP = 26;      // écart entre un anneau replié et l'anneau du lieu choisi
const PIN_W = 30;         // largeur occupée par une épingle au centre du premier anneau

/** Distance minimale entre les centres de deux puces (largeurs wa et wb) alignées selon l'angle t. */
export function clearance(t, wa, wb) {
  return Math.min(((wa + wb) / 2 + 8) / Math.max(Math.abs(Math.cos(t)), 1e-3), CHIP_H / Math.max(Math.abs(Math.sin(t)), 1e-3));
}

/** Anneaux ouverts. Le premier entoure l'épingle. Quand un sous-lieu qui a lui-même des sous-lieux est choisi,
 * ses frères sont repliés (puce « +N autres », `expanded` les redéploie et masque les anneaux plus profonds) :
 * l'anneau du parent se réduit, et le lieu choisi part vers l'extérieur, au centre de son propre anneau
 * (cercle complet, avec un vide du côté du parent). La chaîne avance dans le prolongement des anneaux
 * précédents, infléchie d'au plus 45° vers la direction d'origine du lieu : elle ne revient jamais sur
 * elle-même. Les rayons tiennent compte de la puce au centre. Frères déployés, ou parent à un seul
 * sous-lieu : anneau du lieu choisi en arc autour de sa puce, tourné vers l'extérieur. */
export function layoutRings(path, pins, places, screen, pages, extras, expanded) {
  const rings = [];
  const pin = pins.find((p) => p.place_id === path[0]);
  if (!pin) return rings;
  let at = screen(pin.x, pin.y);
  let from = null;      // centre de l'anneau précédent
  let centerW = PIN_W;  // largeur de ce qui occupe le centre de l'anneau courant
  let link = null;      // anneau précédent replié : { item, angle, radius, w } (lieu choisi à placer)
  const placed = [{ ...at, w: PIN_W }];   // puces déjà posées (et l'épingle), à ne pas recouvrir
  const hits = (x, y, w) => placed.some((c) => Math.abs(c.x - x) < (c.w + w) / 2 + 4 && Math.abs(c.y - y) < CHIP_H - 2);
  for (let level = 0; level < path.length; level++) {
    const placeId = path[level];
    const kids = childrenOf(places, placeId);
    if (!kids.length) break;
    const chosenId = path[level + 1];
    const chosenIndex = chosenId == null ? -1 : kids.findIndex((k) => k.id === chosenId);
    const collapse = chosenIndex >= 0 && !expanded[placeId] && kids.length > 1
      && childrenOf(places, chosenId).length > 0;
    const paged = kids.length > PAGE;
    const pageCount = Math.ceil(kids.length / (PAGE - 1));
    // Page qui contient le lieu choisi s'il est replié (sa direction ne change pas), sinon celle demandée.
    const page = !paged ? 0 : collapse ? Math.floor(chosenIndex / (PAGE - 1)) : (pages[placeId] || 0) % pageCount;
    const shown = paged ? kids.slice(page * (PAGE - 1), (page + 1) * (PAGE - 1)) : kids;
    const widths = shown.map((p) => chipWidth(p.name, extras(p)));
    if (paged) widths.push(56);
    const n = widths.length;
    if (link) centerW = link.item.w;
    const clearAll = (angles) => Math.max(0, ...angles.map((t, i) => clearance(t, centerW, widths[i])));
    let angles;
    let radius;
    const full = !from || Boolean(link);
    if (full) {
      // Premier niveau : en commençant par le haut ; après un parent replié : vide du côté du parent.
      const start = link ? link.angle + Math.PI + Math.PI / n : -Math.PI / 2;
      angles = widths.map((_, i) => start + (2 * Math.PI * i) / n);
      const min = Math.max(link ? 84 : 74, clearAll(angles));
      radius = n === 1 ? min : clamp(ringRadius(angles, widths, true, min), min, 320);
    } else {
      const dir = Math.atan2(at.y - from.y, at.x - from.x);
      // Arc le plus serré possible : élargi tant que le rayon reste grand (au-delà de ±67°, seulement si
      // les puces sont nombreuses, pour ne pas revenir sur l'anneau précédent).
      let span = clamp((n - 1) * 0.35, Math.PI / 4, Math.PI * 0.75);
      for (;;) {
        const a0 = dir - span / 2;
        angles = widths.map((_, i) => (n === 1 ? dir : a0 + (span * i) / (n - 1)));
        radius = n === 1 ? 92 : ringRadius(angles, widths, false, 92);
        const enough = span < Math.PI * 0.75 ? radius <= 130 : radius <= 220;
        if (enough || span >= Math.PI * 1.4) break;
        span = Math.min(Math.PI * 1.4, span + 0.15);
      }
      radius = clamp(Math.max(radius, clearAll(angles)), 92, 320);
    }
    // Repli : direction du lieu choisi (la sienne au premier niveau, sinon vers l'extérieur) et place de
    // « +N autres » (à l'opposé au premier niveau ; ensuite sur le côté, l'opposé étant le lien au parent).
    let outAngle = 0;
    let otherAngle = 0;
    let collapsedR = COLLAPSED_R;
    if (collapse) {
      const own = angles[shown.findIndex((k) => k.id === chosenId)];
      const dirIn = link ? link.angle : from ? Math.atan2(at.y - from.y, at.x - from.x) : null;
      outAngle = dirIn == null ? own : dirIn + clamp(diff0(dirIn, own), -Math.PI / 4, Math.PI / 4);
      otherAngle = dirIn == null ? outAngle + Math.PI : outAngle + (diff0(outAngle, own) > 0.01 ? -1 : 1) * Math.PI / 2;
      collapsedR = Math.max(COLLAPSED_R, clearance(otherAngle, centerW, 96));
    }
    if (link) {
      // Le lieu choisi part vers l'extérieur : son anneau touche presque l'anneau replié du parent, et sa
      // puce ne touche pas celle du centre précédent. Anneau agrandi (et éloigné) tant qu'une de ses puces
      // recouvrirait une puce déjà posée.
      for (let k = 0; k < 16; k++) {
        const d = Math.max(link.radius + (collapse ? collapsedR : radius) + LINK_GAP, clearance(link.angle, link.w, link.item.w));
        at = { x: from.x + d * Math.cos(link.angle), y: from.y + d * Math.sin(link.angle) };
        const around = (r, t, w) => hits(at.x + r * Math.cos(t), at.y + r * Math.sin(t), w);
        const bad = hits(at.x, at.y, centerW)
          || (collapse ? around(collapsedR, otherAngle, 96) : angles.some((t, i) => around(radius, t, widths[i])));
        if (!bad) break;
        if (collapse) collapsedR += 12;
        else radius += 16;
      }
      link.item.x = at.x;
      link.item.y = at.y;
    }
    if (collapse && !link) {
      // « +N autres » sans recouvrir une puce déjà posée : autre côté, puis plus loin.
      const free = (t, r) => !hits(at.x + r * Math.cos(t), at.y + r * Math.sin(t), 96);
      if (level && !free(otherAngle, collapsedR) && free(otherAngle + Math.PI, collapsedR)) otherAngle += Math.PI;
      for (let k = 0; k < 8 && !free(otherAngle, collapsedR); k++) collapsedR += 12;
    }
    if (level) placed.push({ ...at, w: centerW });
    const center = at;
    const pos = (r, t) => ({ x: center.x + r * Math.cos(t), y: center.y + r * Math.sin(t) });
    let items = shown.map((p, i) => ({ place: p, w: widths[i], angle: angles[i], ...pos(radius, angles[i]) }));
    if (paged) items.push({ more: kids.length - shown.length, nextPage: page + 1, w: 56, ...pos(radius, angles[n - 1]) });
    let nextLink = null;
    if (collapse) {
      const chosen = items.find((it) => it.place?.id === chosenId);
      radius = collapsedR;
      items = [chosen, { expand: placeId, count: kids.length - 1, page, w: 96, ...pos(radius, otherAngle) }];
      nextLink = { item: chosen, angle: outAngle, radius, w: centerW };   // position définitive au niveau suivant
    }
    rings.push({ placeId, level, cx: center.x, cy: center.y, radius, items, full: full || collapse,
      from: angles[0] - 0.25, to: angles[n - 1] + 0.25 });
    const next = items.find((it) => it.place?.id === chosenId);
    for (const it of items) if (it !== next) placed.push(it);
    // Frères redéployés : les anneaux plus profonds attendent un nouveau choix (sinon ils recouvriraient
    // celui-ci) ; un clic sur le lieu choisi les rouvre, repliés.
    if (!next || (expanded[placeId] && childrenOf(places, chosenId).length && kids.length > 1)) break;
    from = center;
    at = { x: next.x, y: next.y };
    centerW = next.w;
    link = nextLink;
  }
  return rings;
}

/** Écart signé entre deux angles, ramené entre -π et π. */
export function diff0(a, b) {
  return Math.atan2(Math.sin(b - a), Math.cos(b - a));
}

export function arcPath(cx, cy, r, a0, a1) {
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}`;
}
