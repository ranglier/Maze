import { Icon } from '../ui.jsx';
import { SaveState } from './SaveState.jsx';

/** Barre de la fiche ouverte : fil d'Ariane, autres MJ présents, carte, commentaires, menu ⋯, enregistrement,
 * lecture seule. */
export default function DocBar({ campaignId, campaignName, breadcrumb, others, openThreads, menuOpen, onMenu, status,
  onRetry, readMode, onToggleRead, onGo, onComments }) {
  const cid = campaignId;
  return (
    <div className="doc__bar">
      <nav className="doc__crumbs" aria-label="Fil d'Ariane">
        {breadcrumb.map((b, i) => (
          <span key={b.id}>
            {i > 0 && <span className="doc__crumb-sep">/</span>}
            {i < breadcrumb.length - 1
              ? <button type="button" onClick={() => onGo(b.id)}>{b.kind === 'campaign' ? campaignName : (b.title || 'Sans titre')}</button>
              : <span aria-current="page">{b.kind === 'campaign' ? 'Fiche de campagne' : (b.title || 'Sans titre')}</span>}
          </span>
        ))}
      </nav>
      {others.length > 0 && (
        <div className="presence" aria-label="Également sur cette fiche">
          {others.slice(0, 5).map((p) => (
            <span key={p.id} className="presence__dot" style={{ '--peer': p.color }} title={`${p.name} est sur cette fiche`}>
              {p.name.trim().charAt(0).toUpperCase()}
            </span>
          ))}
          {others.length > 5 && <span className="presence__more">+{others.length - 5}</span>}
        </div>
      )}
      {(() => {
        // Carte du scénario (une page renvoie à celle de son scénario) ; fiche de campagne : carte des factions.
        const scenarioId = breadcrumb.find((b) => b.kind === 'scenario')?.id;
        return (
          <a className="btn btn--small btn--ghost" href={scenarioId ? `#/c/${cid}/carte?scenario=${scenarioId}` : `#/c/${cid}/carte?factions=1`}
            title={scenarioId ? 'Carte des relations du scénario' : 'Carte des factions de la campagne'}>
            <Icon name="graph" size={15} /><span className="btn__label">{scenarioId ? 'Carte du scénario' : 'Carte des factions'}</span>
          </a>
        );
      })()}
      <button type="button" className="btn btn--small btn--ghost doc__comments-btn" onClick={onComments}
        title="Commentaires de la fiche">
        <Icon name="comment" size={15} /> {openThreads > 0 ? openThreads : ''}
      </button>
      <button type="button" className="icon-btn icon-btn--small doc__more" aria-label="Autres actions sur la fiche"
        aria-haspopup="menu" aria-expanded={menuOpen} title="Autres actions"
        onClick={(e) => onMenu(e.currentTarget.getBoundingClientRect())}>
        <Icon name="more" size={17} />
      </button>
      <SaveState status={status} onRetry={onRetry} />
      <button type="button" className={`btn btn--small ${readMode ? 'btn--primary' : 'btn--ghost'}`}
        aria-pressed={readMode} onClick={onToggleRead}
        title={readMode ? 'Repasser en édition' : 'Lecture seule, pratique pendant la partie'}>
        <Icon name={readMode ? 'edit' : 'eye'} size={15} /> {readMode ? 'Modifier' : 'Lecture'}
      </button>
    </div>
  );
}
