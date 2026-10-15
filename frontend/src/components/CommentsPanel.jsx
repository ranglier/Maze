import { useEffect, useRef, useState } from 'react';
import { fmtDate } from './AdminPage.jsx';
import { Icon } from './ui.jsx';
import { TOUCH } from '../viewport.js';

/*
 * Panneau des commentaires d'une fiche : brouillon en cours, fils ouverts (dans l'ordre du texte),
 * fils résolus repliés. Les fils sont ancrés dans le texte par l'éditeur (marques).
 */

const DOC_ORPHAN = { withQuote: 'texte supprimé', empty: 'Texte commenté supprimé' };

const initial = (name) => (name || '?').trim().charAt(0).toUpperCase();

function relative(iso) {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (Number.isNaN(diff)) return '';
  if (diff < 60) return 'à l’instant';
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  return fmtDate(iso);
}

/** Zone de saisie : Ctrl+Entrée pour envoyer, Échap pour annuler. */
function Composer({ placeholder, submitLabel, initialValue = '', autoFocus, onSubmit, onCancel }) {
  const [value, setValue] = useState(initialValue);
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);
  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus]);
  const submit = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    const ok = await onSubmit(value.trim());
    setBusy(false);
    if (ok !== false) setValue('');
  };
  return (
    <div className="composer">
      <textarea ref={ref} rows={2} value={value} placeholder={placeholder} maxLength={5000}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
          if (e.key === 'Escape' && onCancel) { e.preventDefault(); e.stopPropagation(); onCancel(); }
        }} />
      {(value.trim() || onCancel) && (
        <div className="composer__actions">
          {onCancel && <button type="button" className="btn btn--small btn--ghost" onClick={onCancel}>Annuler</button>}
          <button type="button" className="btn btn--small btn--primary" disabled={!value.trim() || busy} onClick={submit}>
            {submitLabel}
          </button>
        </div>
      )}
    </div>
  );
}

function Message({ comment, user, onEdit, onDelete }) {
  const [editing, setEditing] = useState(false);
  const mine = user.role === 'admin' || comment.user_id === user.id;
  return (
    <div className="cmsg">
      <div className="cmsg__head">
        <span className="cmsg__avatar" aria-hidden="true">{initial(comment.author_name)}</span>
        <span className="cmsg__author">{comment.author_name || 'Ancien compte'}</span>
        <span className="cmsg__date" title={fmtDate(comment.created_at)}>{relative(comment.created_at)}</span>
        {mine && !editing && (
          <span className="cmsg__tools">
            <button type="button" className="icon-btn icon-btn--tiny" title="Modifier" aria-label="Modifier le commentaire"
              onClick={() => setEditing(true)}><Icon name="edit" size={13} /></button>
            <button type="button" className="icon-btn icon-btn--tiny icon-btn--danger" title="Supprimer" aria-label="Supprimer le commentaire"
              onClick={() => onDelete(comment)}><Icon name="trash" size={13} /></button>
          </span>
        )}
      </div>
      {editing ? (
        <Composer initialValue={comment.body} submitLabel="Enregistrer" autoFocus
          onCancel={() => setEditing(false)}
          onSubmit={async (body) => { const ok = await onEdit(comment.id, body); if (ok !== false) setEditing(false); return ok; }} />
      ) : (
        <p className="cmsg__body">{comment.body}</p>
      )}
    </div>
  );
}

function Thread({ thread, anchor, active, user, fieldLabel, orphan, onSelect, onReply, onResolve, onEdit, onDelete }) {
  const ref = useRef(null);
  const [replying, setReplying] = useState(false);
  useEffect(() => {
    if (active) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [active]);
  const quote = anchor?.text || thread.quote;
  const label = fieldLabel?.(thread);
  return (
    <li ref={ref} className={`cthread${active ? ' is-active' : ''}${thread.resolved ? ' is-resolved' : ''}`}
      onClick={() => onSelect(thread.id)}>
      <div className="cthread__top">
        <div className="cthread__anchor">
          {label && <span className="cthread__field">{label}</span>}
          {(quote || !anchor) && (
            <blockquote className={`cthread__quote${anchor ? '' : ' is-orphan'}`}>
              {anchor ? quote : (quote ? `${quote} (${orphan.withQuote})` : orphan.empty)}
            </blockquote>
          )}
        </div>
        <button type="button" className="icon-btn icon-btn--tiny" title={thread.resolved ? 'Rouvrir' : 'Résoudre'}
          aria-label={thread.resolved ? 'Rouvrir le fil' : 'Résoudre le fil'}
          onClick={(e) => { e.stopPropagation(); onResolve(thread, !thread.resolved); }}>
          <Icon name={thread.resolved ? 'refresh' : 'check'} size={15} />
        </button>
      </div>
      <Message comment={thread} user={user} onEdit={onEdit} onDelete={onDelete} />
      {thread.replies.map((r) => <Message key={r.id} comment={r} user={user} onEdit={onEdit} onDelete={onDelete} />)}
      {thread.resolved ? (
        <p className="cthread__resolved">Résolu par {thread.resolved_by || '?'}</p>
      ) : (active || replying) ? (
        <div onClick={(e) => e.stopPropagation()}>
          <Composer placeholder="Répondre…" submitLabel="Répondre" autoFocus={replying}
            onSubmit={(body) => onReply(thread.id, body)}
            onCancel={replying ? () => setReplying(false) : undefined} />
        </div>
      ) : thread.replies.length === 0 ? null : (
        <button type="button" className="link-btn cthread__reply" onClick={(e) => { e.stopPropagation(); setReplying(true); onSelect(thread.id); }}>
          Répondre
        </button>
      )}
    </li>
  );
}

export default function CommentsPanel({
  threads, anchors, draft, activeId, user, readOnly,
  onSubmitDraft, onCancelDraft, onSelect, onReply, onResolve, onEdit, onDelete,
  // Fiches PNJ, factions et lieux : section de chaque fil, textes propres, commentaire sur toute la fiche.
  fieldLabel, orphan = DOC_ORPHAN, emptyText, onNewThread,
}) {
  const [showResolved, setShowResolved] = useState(false);
  const pos = (t) => anchors[t.id]?.pos ?? Number.MAX_SAFE_INTEGER;
  const open = (threads || []).filter((t) => !t.resolved).sort((a, b) => pos(a) - pos(b) || a.id - b.id);
  const resolved = (threads || []).filter((t) => t.resolved).sort((a, b) => b.id - a.id);
  const common = { user, fieldLabel, orphan, onSelect, onReply, onResolve, onEdit, onDelete };

  return (
    <div className="comments">
      {draft && (
        <div className="cthread is-active is-draft">
          {(draft.label || draft.quote) && (
            <div className="cthread__anchor">
              {draft.label && <span className="cthread__field">{draft.label}</span>}
              {draft.quote && <blockquote className="cthread__quote">{draft.quote}</blockquote>}
            </div>
          )}
          <Composer placeholder="Votre commentaire…" submitLabel="Commenter" autoFocus
            onSubmit={onSubmitDraft} onCancel={onCancelDraft} />
          <p className="composer__hint">Ctrl+Entrée pour envoyer</p>
        </div>
      )}
      {onNewThread && !draft && !readOnly && (
        <button type="button" className="btn btn--small btn--ghost comments__new" onClick={onNewThread}>
          <Icon name="comment" size={14} /> Commenter la fiche
        </button>
      )}
      {!threads ? <p className="muted comments__empty">Chargement…</p> : open.length === 0 && !draft ? (
        <p className="muted comments__empty">
          {readOnly ? 'Aucun commentaire ouvert.' : emptyText || (TOUCH ? 'Aucun commentaire. Sélectionnez un passage puis touchez la bulle dans la marge.' : 'Aucun commentaire. Sélectionnez un passage puis cliquez sur la bulle dans la marge (ou Ctrl+Alt+M).')}
        </p>
      ) : (
        <ul className="cthreads">
          {open.map((t) => <Thread key={t.id} thread={t} anchor={anchors[t.id]} active={t.id === activeId} {...common} />)}
        </ul>
      )}
      {resolved.length > 0 && (
        <div className="comments__resolved">
          <button type="button" className="link-btn" aria-expanded={showResolved} onClick={() => setShowResolved((v) => !v)}>
            <Icon name={showResolved ? 'chevron' : 'right'} size={13} /> Résolus ({resolved.length})
          </button>
          {showResolved && (
            <ul className="cthreads">
              {resolved.map((t) => <Thread key={t.id} thread={t} anchor={anchors[t.id]} active={t.id === activeId} {...common} />)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
