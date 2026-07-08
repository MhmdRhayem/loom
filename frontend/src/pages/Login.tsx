import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../api'
import { ThemeToggle } from '../App'
import { useAuth } from '../auth'

type Mode = 'signin' | 'signup'

// The demo's three roles with their credentials shown in the open (it's a demo) —
// clicking a row prefills the form. More seeded clients exist (alice/bob/carol,
// password123) for the cross-account isolation scenarios.
const DEMO_ACCOUNTS = [
  {
    role: 'Client',
    hint: 'shops and chats with the assistant',
    email: 'mohammad@example.com',
    password: 'mohammad123',
  },
  {
    role: 'Merchant',
    hint: 'manages the Atelier shop — products page + AI proposals',
    email: 'merchant@example.com',
    password: 'merchant123',
  },
  {
    role: 'Admin',
    hint: 'analytics dashboard, full catalog, user management',
    email: 'admin@example.com',
    password: 'admin123',
  },
]

export default function Login() {
  const { user, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState<Mode>('signin')
  const [name, setName] = useState('')
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
      if (mode === 'signin') await login(email.trim(), password)
      else await register(name.trim(), email.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Too many failed attempts — try again in a few minutes.')
      } else if (err instanceof ApiError && err.status === 409) {
        setError('An account with this email already exists.')
      } else if (err instanceof ApiError && err.status === 422) {
        setError('Please enter a name, a valid email, and a password of 6+ characters.')
      } else {
        setError(mode === 'signin' ? 'Invalid email or password.' : 'Could not create the account.')
      }
      setBusy(false)
    }
  }

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
  }

  const fillDemo = (demoEmail: string, demoPassword: string) => {
    setMode('signin')
    setEmail(demoEmail)
    setPassword(demoPassword)
    setError(null)
  }

  return (
    <div className="login-page">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <div className="login-hero">
        <div className="login-brand">Loom</div>
        <div className="login-tagline">
          One storefront, seven specialists — an adaptive multi-agent shopping assistant.
        </div>
      </div>

      <form className="card login-card" onSubmit={submit}>
        <div className="login-tabs">
          <button
            type="button"
            className={mode === 'signin' ? 'active' : ''}
            onClick={() => switchMode('signin')}
          >
            Sign in
          </button>
          <button
            type="button"
            className={mode === 'signup' ? 'active' : ''}
            onClick={() => switchMode('signup')}
          >
            Create account
          </button>
        </div>

        {mode === 'signup' && (
          <div className="field">
            <label htmlFor="login-name">Name</label>
            <input
              id="login-name"
              autoComplete="name"
              placeholder="Your name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
        )}
        <div className="field">
          <label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        {error && <div className="error-banner login-error">{error}</div>}

        <button
          className="primary login-submit"
          type="submit"
          disabled={busy || !email || !password || (mode === 'signup' && !name)}
        >
          {busy ? '…' : mode === 'signin' ? 'Sign in' : 'Create account'}
        </button>

        {mode === 'signin' && (
          <div className="login-demo">
            <div className="login-demo-label">demo accounts — click one to fill the form</div>
            {DEMO_ACCOUNTS.map((a) => (
              <button
                key={a.email}
                type="button"
                className="login-demo-row"
                title={a.hint}
                onClick={() => fillDemo(a.email, a.password)}
              >
                <span className="login-demo-role">{a.role}</span>
                <span className="login-demo-cred">
                  {a.email} / {a.password}
                </span>
                <span className="login-demo-hint">{a.hint}</span>
              </button>
            ))}
          </div>
        )}
      </form>

      <div className="login-foot muted">
        Your orders, cart, and conversations are private to your account.
      </div>
    </div>
  )
}
