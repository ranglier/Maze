import { useState } from 'react';

/*
 * Glisser-déposer dans une liste en arbre (lieux, personnages).
 * Chaque ligne décrit { key, id, group?, label? } ; `props(row, { inside, accepts })` donne les gestionnaires
 * à poser sur la ligne. Dépôt au-dessus ou en dessous (tiers haut / bas), ou dedans (milieu) si `inside`.
 * `inside: 'only'` : tout dépôt va dedans (racine) ; `row.fixed` : ligne non déplaçable (cible seulement).
 * `onMove(dragged, target, where)` reçoit les deux lignes et 'before' | 'after' | 'inside'.
 */
export function useTreeDrag(onMove) {
  const [drag, setDrag] = useState(null);
  const [drop, setDrop] = useState(null);
  const end = () => { setDrag(null); setDrop(null); };

  const props = (row, { inside = false, accepts = () => true, enabled = true } = {}) => {
    if (!enabled) return {};
    return {
      ...(row.fixed ? {} : { draggable: true }),
      onDragStart: row.fixed ? undefined : (e) => {
        e.stopPropagation();
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', row.label || '');
        setDrag(row);
      },
      onDragEnd: end,
      onDragOver: (e) => {
        if (!drag || drag.key === row.key || !accepts(drag)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const r = e.currentTarget.getBoundingClientRect();
        const y = (e.clientY - r.top) / r.height;
        const where = inside === 'only' ? 'inside' : inside ? (y < 0.3 ? 'before' : y > 0.7 ? 'after' : 'inside') : (y < 0.5 ? 'before' : 'after');
        if (drop?.key !== row.key || drop.where !== where) setDrop({ key: row.key, where });
      },
      onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget) && drop?.key === row.key) setDrop(null); },
      onDrop: (e) => {
        e.preventDefault();
        if (drag && drop?.key === row.key && accepts(drag)) onMove(drag, row, drop.where);
        end();
      },
    };
  };

  return {
    drag,
    props,
    end,
    className: (key) => `${drag?.key === key ? ' is-dragging' : ''}${drop?.key === key ? ` is-drop-${drop.where}` : ''}`,
  };
}
