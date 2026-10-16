import { useEffect, useState } from 'react';
import { t } from '../../i18n/index.js';
import { Icon } from '../ui.jsx';
import { PIN_STYLES } from './constants.js';

/** Commandes sous la carte : zoom, vue d'ensemble, noms des épingles, aspect des épingles (menu). */
export default function MapZoom({ size, zoomAt, fit, labels, onToggleLabels, pinStyle, onPinStyle }) {
  const [styleMenu, setStyleMenu] = useState(false);
  const current = PIN_STYLES.find((x) => x.key === pinStyle);
  useEffect(() => {   // menu « Aspect des épingles » : fermé par un clic ailleurs ou Échap
    if (!styleMenu) return undefined;
    const onDown = (e) => { if (!e.target.closest?.('.map-zoom')) setStyleMenu(false); };
    const onKey = (e) => { if (e.key === 'Escape') setStyleMenu(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [styleMenu]);
  return (
    <div className="map-zoom" onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}>
      <button type="button" className="icon-btn" aria-label={t('Zoomer')} title={t('Zoomer')}
        onClick={() => size && zoomAt(1.5, size.w / 2, size.h / 2)}><Icon name="plus" size={17} /></button>
      <button type="button" className="icon-btn" aria-label={t('Dézoomer')} title={t('Dézoomer')}
        onClick={() => size && zoomAt(1 / 1.5, size.w / 2, size.h / 2)}><Icon name="minus" size={17} /></button>
      <button type="button" className="icon-btn" aria-label={t('Toute la carte')} title={t('Toute la carte')} onClick={fit}>
        <Icon name="fit" size={17} />
      </button>
      <span className="map-zoom__sep" />
      <button type="button" className={`icon-btn${labels ? ' is-active' : ''}`} aria-pressed={labels} onClick={onToggleLabels}
        aria-label={t('Noms des épingles')} title={labels ? t('Masquer les noms des épingles') : t('Afficher les noms des épingles')}>
        <Icon name="heading" size={17} />
      </button>
      <button type="button" className={`icon-btn${styleMenu ? ' is-active' : ''}`} aria-haspopup="menu" aria-expanded={styleMenu}
        aria-label={t('Aspect des épingles')} title={t('Aspect des épingles : {style}', { style: current ? t(current.label) : '' })}
        onClick={() => setStyleMenu((v) => !v)}>
        <Icon name={current?.icon || 'pin'} size={17} />
      </button>
      {styleMenu && (
        <div className="map-style-menu" role="menu" aria-label={t('Aspect des épingles')}>
          <p>{t('Aspect des épingles')}</p>
          {PIN_STYLES.map((x) => (
            <button key={x.key} type="button" role="menuitemradio" aria-checked={pinStyle === x.key}
              className={pinStyle === x.key ? 'is-active' : ''}
              onClick={() => { setStyleMenu(false); if (x.key !== pinStyle) onPinStyle(x.key); }}>
              <Icon name={x.icon} size={15} />
              <span><strong>{t(x.label)}</strong><span>{t(x.hint)}</span></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
