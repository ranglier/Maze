import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon } from './ui.jsx';
import { formatNumber, pt, t } from '../i18n/index.js';

/**
 * Page de dépôt d'une image demandée par Claude (#/depot/<jeton>, outil request_upload du connecteur, uploads.py) :
 * rappel de la cible, choix du fichier (bouton, glisser-déposer, Ctrl+V), aperçu, légende proposée par Claude
 * (modifiable), progression, puis lien vers la fiche. Lien valable 10 minutes, une seule image.
 */

const ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

function targetLine(info) {
  const params = { name: info.target_name, campaign: info.campaign.name };
  switch (info.target) {
    case 'npc': return t('Image pour la fiche du PNJ {name} (campagne {campaign})', params);
    case 'faction': return t('Image pour la fiche de la faction {name} (campagne {campaign})', params);
    case 'place': return t('Image pour la fiche du lieu {name} (campagne {campaign})', params);
    case 'document': return t('Illustration pour la fiche rédigée {name} (campagne {campaign})', params);
    default: return t('Image pour la bibliothèque de la campagne {campaign}', params);
  }
}

/** Adresse de la cible dans Maze (bibliothèque : Administration > Images pour un administrateur). */
function targetHref(info, user) {
  const cid = info.campaign.id;
  switch (info.target) {
    case 'npc': return `#/c/${cid}/personnages/npc-${info.target_id}`;
    case 'faction': return `#/c/${cid}/personnages/faction-${info.target_id}`;
    case 'place': return `#/c/${cid}/lieux/${info.target_id}`;
    case 'document': return `#/c/${cid}/scenarios/${info.target_id}`;
    default: return user.role === 'admin' ? '#/admin/images' : `#/c/${cid}`;
  }
}

function targetLinkLabel(info) {
  if (info.target === 'library') return t('Ouvrir la bibliothèque');
  if (info.target === 'document') return t('Ouvrir la fiche rédigée');
  return t('Ouvrir la fiche');
}

function sizeLabel(bytes) {
  return bytes >= 1024 * 1024
    ? t('{n} Mo', { n: formatNumber(bytes / (1024 * 1024), { maximumFractionDigits: 1 }) })
    : t('{n} Ko', { n: formatNumber(Math.max(1, Math.round(bytes / 1024))) });
}

/** Premier fichier image d'un presse-papiers ou d'un glisser-déposer. */
function imageFile(list) {
  return Array.from(list || []).find((f) => f && (f.type?.startsWith('image/') || !f.type)) || null;
}

export default function UploadPage({ token, user, corner }) {
  const [info, setInfo] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [caption, setCaption] = useState('');
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState(null);
  const [done, setDone] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [loadedAt, setLoadedAt] = useState(() => Date.now());
  const inputRef = useRef(null);

  useEffect(() => { document.title = `${t('Dépôt d’image')} | Maze`; }, []);

  useEffect(() => {
    let alive = true;
    api.uploadTicket(token).then((data) => {
      if (!alive) return;
      setInfo(data);
      setCaption(data.caption || '');
      setLoadedAt(Date.now());
    }).catch((e) => { if (alive) setLoadError(e); });
    return () => { alive = false; };
  }, [token]);

  // Compte à rebours (à la minute près ; plus fin la dernière minute).
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  const left = info ? Math.max(0, info.seconds_left - Math.floor((now - loadedAt) / 1000)) : 0;
  const expired = info?.state === 'expired' || (info?.state === 'pending' && left === 0 && !done && progress === null);
  const open = info?.state === 'pending' && !expired && !done && info.target_name !== null;

  const choose = useCallback((picked) => {
    if (!picked || !info) return;
    setError(null);
    if (picked.type && !ACCEPT.includes(picked.type)) {
      setError(t('Formats acceptés : JPEG, PNG, WebP, GIF'));
      return;
    }
    if (picked.size > info.max_mb * 1024 * 1024) {
      setError(t('Image trop volumineuse (max {size} Mo)', { size: info.max_mb }));
      return;
    }
    setFile(picked);
    setPreview(URL.createObjectURL(picked));
  }, [info]);

  // Coller une image (Ctrl+V) ou la déposer n'importe où sur la page.
  useEffect(() => {
    if (!open || progress !== null) return undefined;
    const onPaste = (e) => {
      const picked = imageFile(e.clipboardData?.files);
      if (picked) { e.preventDefault(); choose(picked); }
    };
    const onOver = (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault();
      setDragging(true);
    };
    const onLeave = (e) => { if (!e.relatedTarget) setDragging(false); };
    const onDrop = (e) => {
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault();
      setDragging(false);
      const picked = imageFile(e.dataTransfer.files);
      if (picked) choose(picked);
      else setError(t('Formats acceptés : JPEG, PNG, WebP, GIF'));
    };
    window.addEventListener('paste', onPaste);
    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('paste', onPaste);
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [open, progress, choose]);

  const send = async () => {
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const out = await api.depositImage(token, file, info.target === 'document' ? null : caption, setProgress);
      setDone(out);
    } catch (e) {
      // Lien expiré ou déjà servi entre-temps : la carte d'état prend le relais.
      if (e.status === 410) setInfo((i) => ({ ...i, state: 'expired' }));
      else if (e.status === 409) setInfo((i) => ({ ...i, state: 'used', image: null }));
      setError(e.message);
    } finally {
      setProgress(null);
    }
  };

  const reset = () => {
    setFile(null);
    setPreview(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  let body;
  if (loadError) {
    body = (
      <div className="upload-page__state" role="alert">
        <Icon name="lock" size={28} />
        <h1>{t('Lien de dépôt inaccessible')}</h1>
        <p>{loadError.message}</p>
        {/* 403 : bibliothèque réservée aux administrateurs, le message du serveur dit quoi faire. */}
        {loadError.status !== 403 && <p className="muted">{t('Le lien est peut-être incomplet, ou cette campagne ne vous est pas ouverte. Demandez un nouveau lien à Claude, ou connectez-vous avec le compte qui a accès à la campagne.')}</p>}
      </div>
    );
  } else if (!info) {
    body = <div className="upload-page__state" aria-busy="true"><p className="muted">{t('Chargement…')}</p></div>;
  } else if (done) {
    body = (
      <div className="upload-page__state upload-page__state--ok" role="status">
        <Icon name="check" size={28} />
        <h1>{t('Image déposée')}</h1>
        <p>{targetLine(done)}</p>
        {done.image && <img className="upload-page__result" src={done.image.thumb_url} alt="" />}
        <p className="muted">{t('Revenez à la conversation et dites à Claude que l’image est déposée : il la retrouvera.')}</p>
        <a className="btn btn--primary" href={targetHref(done, user)}>{targetLinkLabel(done)}</a>
      </div>
    );
  } else if (info.state === 'used') {
    body = (
      <div className="upload-page__state" role="alert">
        <Icon name="image" size={28} />
        <h1>{t('Lien déjà utilisé')}</h1>
        <p>{t('Une image a déjà été déposée avec ce lien. Pour en déposer une autre, demandez un nouveau lien à Claude.')}</p>
        {info.image && <img className="upload-page__result" src={info.image.thumb_url} alt="" />}
        {info.target_name !== null && <a className="btn" href={targetHref(info, user)}>{targetLinkLabel(info)}</a>}
      </div>
    );
  } else if (expired) {
    body = (
      <div className="upload-page__state" role="alert">
        <Icon name="history" size={28} />
        <h1>{t('Lien expiré')}</h1>
        <p>{t('Ce lien n’était valable que 10 minutes. Demandez un nouveau lien à Claude.')}</p>
      </div>
    );
  } else if (info.target_name === null) {
    body = (
      <div className="upload-page__state" role="alert">
        <Icon name="close" size={28} />
        <h1>{t('Fiche introuvable')}</h1>
        <p>{t('La fiche qui devait recevoir l’image a été supprimée. Demandez un nouveau lien à Claude.')}</p>
      </div>
    );
  } else {
    const minutes = Math.ceil(left / 60);
    body = (
      <>
        <header className="upload-page__head">
          <h1>{t('Déposer une image')}</h1>
          <p className="upload-page__target">{targetLine(info)}</p>
          <p className="muted upload-page__timer">
            {left >= 60 ? t('Lien valable encore {n} min, pour une seule image.', { n: minutes })
              : t('Lien valable moins d’une minute encore.')}
          </p>
        </header>
        {!file ? (
          <button type="button" className={`upload-page__drop${dragging ? ' is-dragging' : ''}`}
            onClick={() => inputRef.current?.click()}>
            <Icon name="upload" size={30} />
            <strong>{t('Choisir une image')}</strong>
            <span className="muted">{t('ou glissez-la ici, ou collez-la (Ctrl+V)')}</span>
            <span className="muted upload-page__formats">
              {t('JPEG, PNG, WebP ou GIF, {size} Mo au plus', { size: info.max_mb })}
            </span>
          </button>
        ) : (
          <figure className={`upload-page__preview${dragging ? ' is-dragging' : ''}`}>
            <img src={preview} alt={t('Aperçu de l’image choisie')} />
            <figcaption>
              <span className="upload-page__name">{file.name || t('Image collée')}</span>
              <span className="muted">{sizeLabel(file.size)}</span>
            </figcaption>
          </figure>
        )}
        <input ref={inputRef} type="file" accept={ACCEPT.join(',')} hidden
          onChange={(e) => choose(e.target.files?.[0])} />
        {info.target !== 'document' && (
          <label className="field">
            <span className="field__label">{pt('image', 'Légende')}</span>
            <input type="text" value={caption} maxLength={500} onChange={(e) => setCaption(e.target.value)}
              placeholder={t('Facultative')} disabled={progress !== null} />
          </label>
        )}
        {progress !== null && (
          <div className="upload-page__progress" role="progressbar" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)} aria-label={t('Envoi de l’image')}>
            <span style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        )}
        {error && <p className="form-error upload-page__error" role="alert">{error}</p>}
        <div className="upload-page__actions">
          {file && (
            <button type="button" className="btn btn--ghost" onClick={reset} disabled={progress !== null}>
              {t('Changer d’image')}
            </button>
          )}
          <button type="button" className="btn btn--primary" onClick={send} disabled={!file || progress !== null}>
            <Icon name="upload" size={16} />
            {progress !== null ? t('Envoi… {n} %', { n: Math.round(progress * 100) }) : t('Déposer l’image')}
          </button>
        </div>
      </>
    );
  }

  return (
    <main className="splash upload-page">
      <div className="splash__corner upload-page__corner">
        <a className="btn btn--ghost" href="#/"><Icon name="back" size={16} /><span className="btn__label">{t('Retour aux campagnes')}</span></a>
        {corner}
      </div>
      <section className="splash__card upload-page__card">
        {body}
      </section>
    </main>
  );
}
