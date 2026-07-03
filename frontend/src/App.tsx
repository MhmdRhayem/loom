import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { api } from './api'
import { RequireAuth, useAuth } from './auth'
import Chat from './pages/Chat'
import ConversationDetail from './pages/ConversationDetail'
import Conversations from './pages/Conversations'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import Storefront from './pages/Storefront'
import type { ConversationSummary } from './types'

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

export function TrashIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  )
}

export default function App() {
  const [health, setHealth] = useState<'ok' | 'degraded' | 'down'>('down')
  const [recent, setRecent] = useState<ConversationSummary[]>([])
  const { user, ready, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const activeChat = new URLSearchParams(location.search).get('c')

  // Keep the sidebar's recent-chats list fresh: refetch on sign-in and on every
  // navigation (sending a first message updates the URL to ?c=<id>, landing here too).
  useEffect(() => {
    if (!user) {
      setRecent([])
      return
    }
    api
      .conversations(12)
      .then((res) => setRecent(res.conversations))
      .catch(() => {})
  }, [user, location])

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

  const deleteChat = async (id: string) => {
    if (!window.confirm('Delete this conversation? This cannot be undone.')) return
    try {
      await api.deleteConversation(id)
      setRecent((r) => r.filter((c) => c.id !== id))
      // Deleting the conversation that's open in the chat sends you to a fresh one.
      if (activeChat === id) navigate('/chat', { replace: true })
    } catch {
      // fail-soft: the list refetches on the next navigation anyway
    }
  }

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
          {recent.length > 0 && (
            <div className="recent">
              <div className="recent-label">Recent chats</div>
              <div className="recent-list">
                {recent.map((c) => (
                  <div key={c.id} className={`recent-row ${activeChat === c.id ? 'active' : ''}`}>
                    <Link to={`/chat?c=${c.id}`} className="recent-item" title={c.title ?? c.id}>
                      {c.title || `${c.id.slice(0, 8)}…`}
                    </Link>
                    <button
                      className="icon-btn recent-del"
                      title="Delete conversation"
                      onClick={() => deleteChat(c.id)}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
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
