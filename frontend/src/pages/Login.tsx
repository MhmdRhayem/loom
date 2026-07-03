import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/chat'
  if (user) return <Navigate to={from} replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email.trim(), password)
      navigate(from, { replace: true })
    } catch {
      setError('Invalid email or password.')
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-brand">Loom</div>
      <form className="card login-card" onSubmit={submit}>
        <h3>Sign in</h3>
        <p className="muted">
          Your orders, returns, and conversations are private to your account.
        </p>
        <label htmlFor="login-email">email</label>
        <input
          id="login-email"
          type="email"
          autoComplete="email"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="login-password">password</label>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <div className="error-banner">{error}</div>}
        <button className="primary" type="submit" disabled={busy || !email || !password}>
          {busy ? '…' : 'Sign in'}
        </button>
        <p className="muted login-hint">
          demo accounts: <code>mohammad@example.com</code> / <code>mohammad123</code>,{' '}
          <code>alice@example.com</code> / <code>password123</code> (also bob, carol)
        </p>
      </form>
    </div>
  )
}
