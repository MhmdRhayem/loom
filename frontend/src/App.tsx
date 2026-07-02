import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { api } from './api'
import Chat from './pages/Chat'
import ConversationDetail from './pages/ConversationDetail'
import Conversations from './pages/Conversations'
import Dashboard from './pages/Dashboard'
import Storefront from './pages/Storefront'

const NAV = [
  { to: '/chat', label: 'Chat' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/conversations', label: 'Conversations' },
  { to: '/storefront', label: 'Storefront' },
]

export default function App() {
  const [health, setHealth] = useState<'ok' | 'degraded' | 'down'>('down')

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

  return (
    <div className="app">
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
        <div className="sidebar-foot">multi-agent framework</div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/conversations" element={<Conversations />} />
          <Route path="/conversations/:id" element={<ConversationDetail />} />
          <Route path="/storefront" element={<Storefront />} />
        </Routes>
      </main>
    </div>
  )
}
