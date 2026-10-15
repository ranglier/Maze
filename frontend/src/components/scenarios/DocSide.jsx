import CommentsPanel from '../CommentsPanel.jsx';
import { HistoryPanel } from '../HistoryPanel.jsx';
import { Icon } from '../ui.jsx';

/** Panneau latéral de la fiche ouverte : sommaire, commentaires, historique des versions. */
export default function DocSide({ side, doc, openThreads, headings, activeHeading, onHeading, history, comments }) {
  const { sideTab, sideOpen } = side;
  return (
    <aside className={`doc-side${sideOpen ? ' is-open' : ''}`} aria-label="Panneau latéral">
      <div className="doc-side__tabs" role="tablist">
        <button type="button" role="tab" aria-selected={sideTab === 'toc'} className={sideTab === 'toc' ? 'is-active' : ''}
          onClick={side.selectToc}>
          Sommaire
        </button>
        <button type="button" role="tab" aria-selected={sideTab === 'comments'} className={sideTab === 'comments' ? 'is-active' : ''}
          onClick={side.showComments}>
          Commentaires{openThreads > 0 && <span className="doc-side__badge">{openThreads}</span>}
        </button>
        <button type="button" role="tab" aria-selected={sideTab === 'history'} className={sideTab === 'history' ? 'is-active' : ''}
          onClick={side.showHistory}>
          Historique
        </button>
        <button type="button" className="icon-btn icon-btn--small doc-side__close" aria-label="Fermer le panneau"
          onClick={() => side.setSideOpen(false)}>
          <Icon name="close" size={15} />
        </button>
      </div>
      {sideTab === 'toc' ? (
        <nav className="doc-toc" aria-label="Table des matières">
          {headings.length === 0 ? (
            <p className="muted doc-toc__empty">Les titres de la fiche apparaîtront ici.</p>
          ) : (
            <ul>
              {headings.map((h) => (
                <li key={h.id} className={`doc-toc__l${h.level}${h.id === activeHeading ? ' is-active' : ''}`}>
                  <a href={`#${h.id}`} onClick={(e) => { e.preventDefault(); onHeading(h); }}>
                    {h.text}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </nav>
      ) : sideTab === 'history' && doc ? (
        <HistoryPanel {...history} />
      ) : doc ? (
        <CommentsPanel {...comments} />
      ) : null}
    </aside>
  );
}
