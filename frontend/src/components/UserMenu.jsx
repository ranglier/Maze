import { useEffect, useId, useRef, useState } from 'react';
import { api } from '../api.js';
import { LOCALES, languageName, storeLocale, t } from '../i18n/index.js';
import { initials } from '../util.js';
import { Field, Icon, Modal, SourceLink, useAction } from './ui.jsx';
import { useSheet } from '../viewport.js';

// Libellés traduits à la lecture (affichage), pas au chargement du module.
export const ROLE_LABEL = {
  get admin() { return t('Administrateur'); },
  get mj() { return t('MJ'); },
};

/** Choix de langue : « Automatique » (vide : navigateur, puis langue de l'instance), puis chaque langue connue, sous
 * son propre nom (Français, English). */
export const localeChoices = () => [['', t('Automatique')], ...LOCALES.map((l) => [l, languageName(l)])];

export default function UserMenu({ user, onLogout, onAdmin, sourceUrl, onLocale }) {
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState(false);
  const ref = useRef(null);
  const langId = useId();
  const run = useAction();
  useSheet(open);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  // Langue du compte (PUT /api/auth/locale), gardée aussi dans ce navigateur pour l'écran de connexion.
  const pickLocale = async (locale) => {
    if (locale === (user.locale || '')) return;
    const done = await run(() => api.setLocale(locale));
    if (!done) return;
    storeLocale(locale);
    onLocale?.(locale);
  };

  return (
    <div className="popover-anchor user-menu" ref={ref}>
      <button type="button" className="user-menu__trigger" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        title={user.display_name || user.username}>
        <span className="user-menu__avatar" aria-hidden="true">{initials(user.display_name || user.username)}</span>
        <span className="sr-only">{t('Menu du compte')}</span>
      </button>
      {open && (
        <div className="popover" role="menu">
          <div className="user-menu__who">
            <strong>{user.display_name || user.username}</strong>
            <span>{user.username}, {ROLE_LABEL[user.role]}</span>
          </div>
          <div className="popover__group popover__group--actions">
            <button type="button" role="menuitem" onClick={() => { setOpen(false); setAccount(true); }}>
              <Icon name="lock" size={15} /> {t('Changer mon mot de passe')}
            </button>
            {user.role === 'admin' && onAdmin && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); onAdmin(); }}>
                <Icon name="layout" size={15} /> {t('Administration')}
              </button>
            )}
            <button type="button" role="menuitem" onClick={onLogout}>
              <Icon name="logout" size={15} /> {t('Se déconnecter')}
            </button>
          </div>
          <div className="popover__group user-menu__lang" role="group" aria-labelledby={langId} data-locale-menu>
            <span className="popover__title" id={langId}><Icon name="globe" size={14} /> {t('Langue')}</span>
            <div className="segmented segmented--compact">
              {localeChoices().map(([value, label]) => (
                <button key={value || 'auto'} type="button" role="menuitemradio"
                  aria-checked={(user.locale || '') === value} className={(user.locale || '') === value ? 'is-active' : ''}
                  lang={value || undefined} onClick={() => pickLocale(value)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {sourceUrl && (
            <div className="popover__group popover__group--actions user-menu__about">
              <SourceLink url={sourceUrl} role="menuitem" onClick={() => setOpen(false)}>
                <Icon name="code" size={15} />
              </SourceLink>
            </div>
          )}
        </div>
      )}
      {account && <PasswordDialog onClose={() => setAccount(false)} />}
    </div>
  );
}

function PasswordDialog({ onClose }) {
  const run = useAction();
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const mismatch = form.confirm && form.next !== form.confirm;
  const valid = form.current && form.next.length >= 8 && form.next === form.confirm;

  const submit = async (e) => {
    e.preventDefault();
    if (!valid) return;
    const done = await run(() => api.changePassword(form.current, form.next), t('Mot de passe modifié'));
    if (done) onClose();
  };

  return (
    <Modal title={t('Changer mon mot de passe')} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="submit" form="password-form" className="btn btn--primary" disabled={!valid}>{t('Enregistrer')}</button>
      </>
    )}>
      <form id="password-form" onSubmit={submit}>
        <Field label={t('Mot de passe actuel')} htmlFor="pw-current">
          <input id="pw-current" type="password" autoComplete="current-password" value={form.current} onChange={set('current')} />
        </Field>
        <Field label={t('Nouveau mot de passe')} htmlFor="pw-next" hint={t('8 caractères minimum.')}>
          <input id="pw-next" type="password" autoComplete="new-password" value={form.next} onChange={set('next')} />
        </Field>
        <Field label={t('Confirmation')} htmlFor="pw-confirm">
          <input id="pw-confirm" type="password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} />
        </Field>
        {mismatch && <p className="form-error">{t('Les deux mots de passe ne correspondent pas.')}</p>}
        <p className="field__hint">{t('Vos autres sessions ouvertes (autres navigateurs) seront déconnectées.')}</p>
      </form>
    </Modal>
  );
}
