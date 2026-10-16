import { t } from '../i18n/index.js';
import { Icon, Modal } from './ui.jsx';

/**
 * « Déplacer vers… » : choix d'un nouveau parent dans une arborescence (fiches, lieux).
 * Utile partout, indispensable sur écran tactile où le glisser-déposer de l'arbre n'existe pas.
 * `options` : [{ id, label, depth, icon, current, disabled }] dans l'ordre d'affichage.
 */
export default function MoveDialog({ title, intro, options, onPick, onClose }) {
  return (
    <Modal title={title} onClose={onClose}>
      {intro && <p className="muted move-list__intro">{intro}</p>}
      <ul className="move-list">
        {options.map((o) => (
          <li key={o.id ?? 'root'}>
            <button type="button" disabled={o.disabled || o.current} style={{ '--depth': o.depth || 0 }}
              aria-current={o.current ? 'true' : undefined} onClick={() => onPick(o.id)}>
              <Icon name={o.icon || 'page'} size={16} />
              <span className="move-list__label">{o.label}</span>
              {o.current && <span className="move-list__tag">{t('actuel')}</span>}
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

/**
 * Ordre d'affichage d'une arborescence (`children(parent)` : enfants triés) ;
 * l'élément `exclude` et ses descendants sont retirés.
 */
export function flattenTree(children, rootParent, exclude) {
  const out = [];
  const walk = (parent, depth) => {
    for (const it of children(parent)) {
      if (it.id === exclude) continue;
      out.push({ item: it, depth });
      walk(it.id, depth + 1);
    }
  };
  walk(rootParent, 0);
  return out;
}
