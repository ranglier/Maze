import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../ui.jsx';
import { t } from '../../i18n/index.js';
import { useSheet } from '../../viewport.js';
import MoveDialog, { flattenTree } from '../MoveDialog.jsx';
import { DOC_STATUS, childrenOf } from './docs.js';

// --- Arbre des fiches ------------------------------------------------------------------

export default function DocTree({ tree, campaignName, currentId, onOpen, onCreate, onDuplicate, onMove, onMoveTo, onDelete }) {
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [menu, setMenu] = useState(null);
  const [moving, setMoving] = useState(null); // fiche à déplacer (« Déplacer vers… »)
  const [drag, setDrag] = useState(null); // identifiant de la fiche déplacée
  const [drop, setDrop] = useState(null); // { id, where: 'before' | 'after' | 'inside' }
  if (!tree) return <aside className="doc-tree" aria-busy="true" />;
  const docs = tree.documents;
  const root = docs.find((d) => d.id === tree.root_id);

  // --- Glisser-déposer : au-dessus / en dessous d'une fiche, ou dedans (milieu de la ligne).
  const within = (id, ancestorId) => {
    for (let cur = docs.find((d) => d.id === id); cur; cur = docs.find((d) => d.id === cur.parent_id)) {
      if (cur.id === ancestorId) return true;
    }
    return false;
  };
  const endDrag = () => { setDrag(null); setDrop(null); };
  const dragProps = (d) => ({
    draggable: true,
    onDragStart: (e) => {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', d.title || t('Sans titre'));
      setDrag(d.id);
    },
    onDragEnd: endDrag,
    onDragOver: (e) => {
      if (!drag || within(d.id, drag)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const r = e.currentTarget.getBoundingClientRect();
      const y = (e.clientY - r.top) / r.height;
      const where = y < 0.3 ? 'before' : y > 0.7 ? 'after' : 'inside';
      if (drop?.id !== d.id || drop.where !== where) setDrop({ id: d.id, where });
    },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget) && drop?.id === d.id) setDrop(null); },
    onDrop: (e) => {
      e.preventDefault();
      if (!drag || !drop || within(d.id, drag)) { endDrag(); return; }
      if (drop.where === 'inside') {
        moveInto(drag, d.id);
      } else {
        const siblings = childrenOf(docs, d.parent_id).filter((x) => x.id !== drag);
        const at = siblings.findIndex((x) => x.id === d.id) + (drop.where === 'after' ? 1 : 0);
        onMoveTo(drag, d.parent_id, at);
      }
      endDrag();
    },
  });
  // Déposée sur la fiche de campagne : devient le dernier scénario.
  const rootDropProps = {
    onDragOver: (e) => { if (drag) { e.preventDefault(); if (drop?.id !== root.id) setDrop({ id: root.id, where: 'inside' }); } },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget) && drop?.id === root.id) setDrop(null); },
    onDrop: (e) => { e.preventDefault(); if (drag) onMoveTo(drag, root.id, 100000); endDrag(); },
  };
  const dropClass = (id) => (drop?.id === id ? ` is-drop-${drop.where}` : '');
  // Fiche déposée dans une autre : celle-ci n'est dépliée que si le serveur accepte le déplacement.
  const moveInto = async (id, parentId) => {
    if (await onMoveTo(id, parentId, 100000)) setCollapsed((s) => { const n = new Set(s); n.delete(parentId); return n; });
  };

  const renderItems = (parentId, depth) => childrenOf(docs, parentId).map((d, i, arr) => {
    const kids = childrenOf(docs, d.id);
    const open = !collapsed.has(d.id);
    return (
      <li key={d.id}>
        <div className={`doc-tree__item${d.id === currentId ? ' is-active' : ''}${drag === d.id ? ' is-dragging' : ''}${dropClass(d.id)}`}
          style={{ '--depth': depth }} title={t('Glisser pour déplacer')} {...dragProps(d)}>
          {kids.length > 0 ? (
            <button type="button" className="doc-tree__caret" aria-label={open ? t('Replier') : t('Déplier')} aria-expanded={open}
              onClick={() => setCollapsed((s) => { const n = new Set(s); if (n.has(d.id)) n.delete(d.id); else n.add(d.id); return n; })}>
              <Icon name={open ? 'chevron' : 'right'} size={13} />
            </button>
          ) : <span className="doc-tree__caret" />}
          <button type="button" className="doc-tree__link" onClick={() => onOpen(d.id)}
            aria-current={d.id === currentId ? 'page' : undefined}>
            <span className={`status-dot status--${d.status}`} title={DOC_STATUS[d.status]?.label} />
            <span className="doc-tree__label">{d.title || t('Sans titre')}</span>
          </button>
          <button type="button" className="doc-tree__more" aria-label={t('Actions pour {title}', { title: d.title })}
            onClick={(e) => setMenu({ item: d, rect: e.currentTarget.getBoundingClientRect(), first: i === 0, last: i === arr.length - 1 })}>
            <Icon name="more" size={16} />
          </button>
        </div>
        {kids.length > 0 && open && <ul>{renderItems(d.id, depth + 1)}</ul>}
      </li>
    );
  });

  return (
    <aside className="doc-tree" aria-label={t('Fiches de la campagne')}>
      <button type="button" className={`doc-tree__root${root?.id === currentId ? ' is-active' : ''}${dropClass(root?.id)}`}
        onClick={() => onOpen(root.id)} {...rootDropProps}>
        <Icon name="book" size={17} />
        <span>
          <strong>{t('Fiche de campagne')}</strong>
          <span className="doc-tree__sub">{campaignName}</span>
        </span>
      </button>
      <div className="doc-tree__section">
        <span>{t('Scénarios')}</span>
        <button type="button" className="icon-btn icon-btn--small" title={t('Nouveau scénario')} aria-label={t('Nouveau scénario')}
          onClick={() => onCreate(root.id)}>
          <Icon name="plus" size={15} />
        </button>
      </div>
      <ul className="doc-tree__list">{renderItems(root.id, 0)}</ul>
      {childrenOf(docs, root.id).length === 0 && (
        <button type="button" className="doc-tree__empty" onClick={() => onCreate(root.id)}>
          <Icon name="plus" size={14} /> {t('Créer le premier scénario')}
        </button>
      )}
      {menu && (
        <TreeMenu menu={menu} onClose={() => setMenu(null)}
          onAction={(action) => {
            setMenu(null);
            if (action === 'child') onCreate(menu.item.id);
            if (action === 'duplicate') onDuplicate(menu.item);
            if (action === 'up') onMove(menu.item, -1);
            if (action === 'down') onMove(menu.item, 1);
            if (action === 'move') setMoving(menu.item);
            if (action === 'delete') onDelete(menu.item);
          }} />
      )}
      {moving && (
        <MoveDialog title={t('Déplacer « {title} »', { title: moving.title || t('Sans titre') })}
          intro={t('Choisissez la fiche qui la contiendra. Sous la fiche de campagne, elle devient un scénario.')}
          options={[
            { id: root.id, label: t('Fiche de campagne'), icon: 'book', depth: 0, current: moving.parent_id === root.id },
            ...flattenTree((p) => childrenOf(docs, p), root.id, moving.id).map(({ item, depth }) => ({
              id: item.id, label: item.title || t('Sans titre'), depth: depth + 1, current: moving.parent_id === item.id,
            })),
          ]}
          onClose={() => setMoving(null)}
          onPick={(parentId) => {
            setMoving(null);
            moveInto(moving.id, parentId);
          }} />
      )}
    </aside>
  );
}

function TreeMenu({ menu, onClose, onAction }) {
  const ref = useRef(null);
  useSheet();
  useEffect(() => {
    const onDown = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [onClose]);
  return createPortal(
    <div className="popover popover--menu" ref={ref} role="menu"
      style={{ position: 'fixed', top: menu.rect.bottom + 4, left: Math.min(menu.rect.left, window.innerWidth - 240), right: 'auto' }}>
      <div className="popover__group popover__group--actions">
        <button type="button" role="menuitem" onClick={() => onAction('child')}><Icon name="plus" size={15} /> {t('Ajouter une sous-page')}</button>
        <button type="button" role="menuitem" onClick={() => onAction('duplicate')}><Icon name="copy" size={15} /> {t('Dupliquer')}</button>
        <button type="button" role="menuitem" disabled={menu.first} onClick={() => onAction('up')}><Icon name="up" size={15} /> {t('Monter')}</button>
        <button type="button" role="menuitem" disabled={menu.last} onClick={() => onAction('down')}><Icon name="down" size={15} /> {t('Descendre')}</button>
        <button type="button" role="menuitem" onClick={() => onAction('move')}><Icon name="swap" size={15} /> {t('Déplacer vers…')}</button>
        <button type="button" role="menuitem" className="danger-text" onClick={() => onAction('delete')}><Icon name="trash" size={15} /> {t('Supprimer')}</button>
      </div>
    </div>,
    document.body,
  );
}
