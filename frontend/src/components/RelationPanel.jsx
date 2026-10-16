import { api } from '../api.js';
import { typeOf } from '../relationTypes.js';
import { nodeId } from '../util.js';
import { Icon, Markdown, useAction, useConfirm } from './ui.jsx';
import { t } from '../i18n/index.js';

export default function RelationPanel({
  relation, types, nodeName, resolveRef, onClose, onOpenNode, onEdit, onChanged, onDeleted,
}) {
  const run = useAction();
  const confirm = useConfirm();
  const type = typeOf(types, relation.type);
  const source = nodeId(relation.source_kind, relation.source_id);
  const target = nodeId(relation.target_kind, relation.target_id);

  const swap = async () => {
    await run(() => api.updateRelation(relation.id, {
      source_kind: relation.target_kind, source_id: relation.target_id,
      target_kind: relation.source_kind, target_id: relation.source_id,
    }), t('Sens inversé'));
    onChanged();
  };

  const remove = async () => {
    const ok = await confirm({
      title: t('Supprimer la relation'),
      message: t('Le lien entre {source} et {target} sera supprimé.', { source: nodeName(source), target: nodeName(target) }),
    });
    if (!ok) return;
    const done = await run(() => api.deleteRelation(relation.id), t('Relation supprimée'));
    if (done !== undefined) onDeleted();
  };

  return (
    <aside className="panel panel--relation" aria-label={t('Relation')} style={{ '--rel': type.color }}>
      <div className="panel__scroll">
        <button type="button" className="icon-btn panel__close" onClick={onClose} aria-label={t('Fermer')}>
          <Icon name="close" />
        </button>
        <p className="sheet-head__kind">{t('Relation')}</p>
        <div className="relation-hero">
          <button type="button" className="relation-hero__end" onClick={() => onOpenNode(source)}>{nodeName(source)}</button>
          <div className="relation-hero__link">
            <span className="relation-hero__line" data-directed={relation.directed || undefined} />
            <span className="relation-hero__type">{type.label}</span>
          </div>
          <button type="button" className="relation-hero__end" onClick={() => onOpenNode(target)}>{nodeName(target)}</button>
        </div>
        {relation.label && relation.label !== type.label && <h2 className="relation-label">{relation.label}</h2>}
        <ul className="relation-facts">
          <li>{relation.directed ? (type.hint ? t('Sens unique : {hint}', { hint: type.hint }) : t('Sens unique')) : t('Lien réciproque')}</li>
          {relation.secret && <li className="relation-facts__secret"><Icon name="lock" size={13} /> {t('Secret, connu du MJ seulement')}</li>}
        </ul>
        {relation.description
          ? <Markdown text={relation.description} resolve={resolveRef} onOpenNode={onOpenNode} />
          : <p className="muted">{t('Aucun détail. Modifiez la relation pour en ajouter.')}</p>}
      </div>
      <footer className="panel__actions">
        <button type="button" className="btn btn--primary" onClick={onEdit}><Icon name="edit" size={16} /> {t('Modifier')}</button>
        {relation.directed && (
          <button type="button" className="btn btn--ghost" onClick={swap}><Icon name="swap" size={16} /> {t('Inverser')}</button>
        )}
        <button type="button" className="icon-btn icon-btn--danger" onClick={remove} aria-label={t('Supprimer la relation')} title={t('Supprimer')}>
          <Icon name="trash" />
        </button>
      </footer>
    </aside>
  );
}
