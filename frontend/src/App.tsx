import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import { RequireAuth, useAuth } from './auth'
import Chat from './pages/Chat'
import ConversationDetail from './pages/ConversationDetail'
import Conversations from './pages/Conversations'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import Storefront from './pages/Storefront'

const NAV = [
  { to: '/chat', label: 'Chat' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/conversations', label: 'Conversations' },
  { to: '/storefront', label: 'Storefront' },
]

const initials = (name: string) =>
  name
    .split(' ')
    .filter(Boolean)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

export default function App() {
  const [health, setHealth] = useState<'ok' | 'degraded' | 'down'>('down')
  const { user, ready, logout } = useAuth()

  useEffect(() => {
    let cancelled = false
    const check = () =>
      api
        .health()
        .then((h) => !cancelled && setHealth(h.status === 'ok' ? 'ok' : 'degraded'))
        .catch(() => !cancelled && setHealth('down'))
    check()
    const id = setInterval(check, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // Don't flash the wrong layout while the stored token is being validated.
  if (!ready) return null

  return (
    <div className="app">
      {user && (
        <aside className="sidebar">
          <div className="brand">
            Loom
            <span className={`health-dot ${health}`} title={`API: ${health}`} />
          </div>
          <nav>
            {NAV.map(({ to, label }) => (
              <NavLink key={to} to={to} className={({ isActive }) => (isActive ? 'active' : '')}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="sidebar-user" title={user.email}>
              <span className="avatar">{initials(user.name)}</span>
              <span className="user-name">{user.name}</span>
              <button className="signout" onClick={logout} title="Sign out">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                  <polyline points="16 17 21 12 16 7" />
                  <line x1="21" y1="12" x2="9" y2="12" />
                </svg>
              </button>
            </div>
            <div>multi-agent framework</div>
          </div>
        </aside>
      )}
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/login" element={<Login />} />
          <Route
            path="/chat"
            element={
              <RequireAuth>
                <Chat />
              </RequireAuth>
            }
          />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route
            path="/conversations"
            element={
              <RequireAuth>
                <Conversations />
              </RequireAuth>
            }
          />
          <Route
            path="/conversations/:id"
            element={
              <RequireAuth>
                <ConversationDetail />
              </RequireAuth>
            }
          />
          <Route path="/storefront" element={<Storefront />} />
        </Routes>
      </main>
    </div>
  )
}
