import { useState } from 'react';
import { api } from '../api.js';
import { applyPreferences, storeLocale, storedLocale, t } from '../i18n/index.js';
import { Icon, SourceLink } from './ui.jsx';
import { localeChoices } from './UserMenu.jsx';

export default function LoginScreen({ onLoggedIn, sourceUrl }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username, password);
      onLoggedIn();
    } catch (err) {
      setError(err.message);
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="splash">
      <form className="splash__card" onSubmit={submit}>
        <h1 className="wordmark">Maze</h1>
        <p className="splash__lead">{t("L'atelier du meneur de jeu.")}</p>
        <label className="field__label" htmlFor="login-username">{t('Identifiant')}</label>
        <input id="login-username" autoComplete="username" autoFocus autoCapitalize="none" spellCheck={false}
          value={username} onChange={(e) => setUsername(e.target.value)} required />
        <label className="field__label" htmlFor="login-password">{t('Mot de passe')}</label>
        <input id="login-password" type="password" autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn--primary btn--block" disabled={busy || !password || !username}>
          {busy ? t('Vérification…') : t('Se connecter')}
        </button>
        <SplashFooter sourceUrl={sourceUrl} />
      </form>
    </main>
  );
}

/** Pied de l'écran de connexion : langue de l'interface, et code source de l'instance, visible avant toute connexion
 * (AGPL, section 13). */
function SplashFooter({ sourceUrl }) {
  return (
    <>
      <LanguageSelect />
      {sourceUrl && <p className="splash__footer"><SourceLink url={sourceUrl} /></p>}
    </>
  );
}

/** Langue avant connexion : gardée dans ce navigateur (localStorage) ; le réglage du compte l'emporte ensuite. */
function LanguageSelect() {
  const [choice, setChoice] = useState(storedLocale);
  const change = (e) => {
    setChoice(e.target.value);
    storeLocale(e.target.value);
    applyPreferences();
  };
  return (
    <p className="splash__footer splash__lang">
      <Icon name="globe" size={14} />
      <select aria-label={t('Langue')} value={choice} onChange={change} data-locale-select>
        {localeChoices().map(([value, label]) => <option key={value || 'auto'} value={value}>{label}</option>)}
      </select>
    </p>
  );
}

/** Premier lancement sans aucun compte : création de l'administrateur. */
export function SetupScreen({ onDone, sourceUrl }) {
  const [form, setForm] = useState({ username: '', display_name: '', password: '', confirm: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const mismatch = form.confirm && form.password !== form.confirm;

  const submit = async (e) => {
    e.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    try {
      await api.setup({ username: form.username, display_name: form.display_name, password: form.password });
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="splash">
      <form className="splash__card splash__card--wide" onSubmit={submit}>
        <h1 className="wordmark">Maze</h1>
        <p className="splash__lead">{t('Premier lancement : créez le compte administrateur.')}</p>
        <label className="field__label" htmlFor="setup-username">{t('Identifiant')}</label>
        <input id="setup-username" autoComplete="username" autoFocus autoCapitalize="none" spellCheck={false}
          value={form.username} onChange={set('username')} required />
        <label className="field__label" htmlFor="setup-name">{t('Nom affiché')}</label>
        <input id="setup-name" value={form.display_name} onChange={set('display_name')} />
        <label className="field__label" htmlFor="setup-password">{t('Mot de passe (8 caractères minimum)')}</label>
        <input id="setup-password" type="password" autoComplete="new-password" minLength={8}
          value={form.password} onChange={set('password')} required />
        <label className="field__label" htmlFor="setup-confirm">{t('Confirmation')}</label>
        <input id="setup-confirm" type="password" autoComplete="new-password"
          value={form.confirm} onChange={set('confirm')} required />
        {mismatch && <p className="form-error">{t('Les deux mots de passe ne correspondent pas.')}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn--primary btn--block"
          disabled={busy || mismatch || form.password.length < 8 || !form.username}>
          {t('Créer le compte et entrer')}
        </button>
        <SplashFooter sourceUrl={sourceUrl} />
      </form>
    </main>
  );
}
