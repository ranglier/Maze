import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { initials } from '../util.js';
import { Field, Icon, Modal, useAction } from './ui.jsx';
import { useSheet } from '../viewport.js';

export const ROLE_LABEL = { admin: 'Administrateur', mj: 'MJ' };

export default function UserMenu({ user, onLogout, onAdmin }) {
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState(false);
  const ref = useRef(null);
  useSheet(open);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div className="popover-anchor user-menu" ref={ref}>
      <button type="button" className="user-menu__trigger" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        title={user.display_name || user.username}>
        <span className="user-menu__avatar" aria-hidden="true">{initials(user.display_name || user.username)}</span>
        <span className="sr-only">Menu du compte</span>
      </button>
      {open && (
        <div className="popover" role="menu">
          <div className="user-menu__who">
            <strong>{user.display_name || user.username}</strong>
            <span>{user.username}, {ROLE_LABEL[user.role]}</span>
          </div>
          <div className="popover__group popover__group--actions">
            <button type="button" role="menuitem" onClick={() => { setOpen(false); setAccount(true); }}>
              <Icon name="lock" size={15} /> Changer mon mot de passe
            </button>
            {user.role === 'admin' && onAdmin && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); onAdmin(); }}>
                <Icon name="layout" size={15} /> Administration
              </button>
            )}
            <button type="button" role="menuitem" onClick={onLogout}>
              <Icon name="logout" size={15} /> Se déconnecter
            </button>
          </div>
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
    const done = await run(() => api.changePassword(form.current, form.next), 'Mot de passe modifié');
    if (done) onClose();
  };

  return (
    <Modal title="Changer mon mot de passe" onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="submit" form="password-form" className="btn btn--primary" disabled={!valid}>Enregistrer</button>
      </>
    )}>
      <form id="password-form" onSubmit={submit}>
        <Field label="Mot de passe actuel" htmlFor="pw-current">
          <input id="pw-current" type="password" autoComplete="current-password" value={form.current} onChange={set('current')} />
        </Field>
        <Field label="Nouveau mot de passe" htmlFor="pw-next" hint="8 caractères minimum.">
          <input id="pw-next" type="password" autoComplete="new-password" value={form.next} onChange={set('next')} />
        </Field>
        <Field label="Confirmation" htmlFor="pw-confirm">
          <input id="pw-confirm" type="password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} />
        </Field>
        {mismatch && <p className="form-error">Les deux mots de passe ne correspondent pas.</p>}
        <p className="field__hint">Vos autres sessions ouvertes (autres navigateurs) seront déconnectées.</p>
      </form>
    </Modal>
  );
}
