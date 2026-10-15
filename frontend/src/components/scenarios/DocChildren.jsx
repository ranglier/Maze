import { fmtDate } from '../AdminPage.jsx';
import { Icon } from '../ui.jsx';
import { DOC_STATUS, childrenOf } from './docs.js';

/** Scénarios de la fiche de campagne, ou sous-pages d'une fiche, en cartes ; bouton de création. */
export function DocChildren({ isRoot, readMode, docs, items, onCreate, onGo }) {
  return (
    <section className="doc-children">
      <div className="doc-children__head">
        <h2>{isRoot ? 'Scénarios' : 'Sous-pages'}</h2>
        {!readMode && (
          <button type="button" className="btn btn--small btn--ghost" onClick={onCreate}>
            <Icon name="plus" size={14} /> {isRoot ? 'Nouveau scénario' : 'Nouvelle sous-page'}
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="muted">
          {isRoot ? 'Aucun scénario pour l’instant.' : 'Découpez le scénario en actes, scènes ou annexes avec des sous-pages.'}
        </p>
      ) : (
        <ul className="doc-cards">
          {items.map((c) => (
            <li key={c.id}>
              <button type="button" onClick={() => onGo(c.id)}>
                <span className="doc-cards__title">{c.title || 'Sans titre'}</span>
                <span className={`status-chip status--${c.status}`}><span className="status-dot" />{DOC_STATUS[c.status]?.label}</span>
                <span className="doc-cards__meta">
                  {childrenOf(docs, c.id).length > 0 && `${childrenOf(docs, c.id).length} sous-page(s), `}
                  modifié le {fmtDate(c.updated_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** « Fiches qui renvoient ici » (renvois doc: d'autres fiches rédigées). */
export function DocLinks({ links, docs, onGo }) {
  if (!links?.length) return null;
  return (
    <section className="doc-children doc-links">
      <div className="doc-children__head"><h2>Fiches qui renvoient ici</h2></div>
      <ul className="doc-links__list">
        {links.map((l) => (
          <li key={l.id}>
            <button type="button" className="doc-links__item" onClick={() => onGo(l.id)}>
              <Icon name="book" size={14} />
              <span>{l.kind === 'campaign' ? 'Fiche de campagne' : (docs?.find((d) => d.id === l.id)?.title || l.title || 'Sans titre')}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
