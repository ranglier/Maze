import { useState } from 'react';
import { api } from '../api.js';

export default function LoginScreen({ onLoggedIn }) {
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
        <p className="splash__lead">L'atelier du meneur de jeu.</p>
        <label className="field__label" htmlFor="login-username">Identifiant</label>
        <input id="login-username" autoComplete="username" autoFocus autoCapitalize="none" spellCheck={false}
          value={username} onChange={(e) => setUsername(e.target.value)} required />
        <label className="field__label" htmlFor="login-password">Mot de passe</label>
        <input id="login-password" type="password" autoComplete="current-password"
          value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn--primary btn--block" disabled={busy || !password || !username}>
          {busy ? 'Vérification…' : 'Se connecter'}
        </button>
      </form>
    </main>
  );
}

/** Premier lancement sans aucun compte : création de l'administrateur. */
export function SetupScreen({ onDone }) {
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
        <p className="splash__lead">Premier lancement : créez le compte administrateur.</p>
        <label className="field__label" htmlFor="setup-username">Identifiant</label>
        <input id="setup-username" autoComplete="username" autoFocus autoCapitalize="none" spellCheck={false}
          value={form.username} onChange={set('username')} required />
        <label className="field__label" htmlFor="setup-name">Nom affiché</label>
        <input id="setup-name" value={form.display_name} onChange={set('display_name')} />
        <label className="field__label" htmlFor="setup-password">Mot de passe (8 caractères minimum)</label>
        <input id="setup-password" type="password" autoComplete="new-password" minLength={8}
          value={form.password} onChange={set('password')} required />
        <label className="field__label" htmlFor="setup-confirm">Confirmation</label>
        <input id="setup-confirm" type="password" autoComplete="new-password"
          value={form.confirm} onChange={set('confirm')} required />
        {mismatch && <p className="form-error">Les deux mots de passe ne correspondent pas.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="submit" className="btn btn--primary btn--block"
          disabled={busy || mismatch || form.password.length < 8 || !form.username}>
          Créer le compte et entrer
        </button>
      </form>
    </main>
  );
}
