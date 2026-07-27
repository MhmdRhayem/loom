import { Suspense, lazy, useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { api } from './api'
import { RequireAuth, useAuth } from './auth'
import Chat from './pages/Chat'
import ConversationDetail from './pages/ConversationDetail'
import Conversations from './pages/Conversations'
import Login from './pages/Login'
import Products from './pages/Products'
import Storefront from './pages/Storefront'
import Users from './pages/Users'
import type { ConversationSummary, Role } from './types'

// The dashboards pull in the whole charting library — split them out of the main
// bundle so the chat/storefront pages don't pay for it.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ShopDashboard = lazy(() => import('./pages/ShopDashboard'))

// Sidebar links per role: clients shop and chat; merchants also manage their shop's
// products and see its sales dashboard; admins get the analytics dashboard and users.
// Sections with a heading render their items as an indented sublist; the storefront
// and shop-management views are routes of their own rather than in-page tabs.
interface NavItem {
  to: string
  label: string
  roles?: Role[]
  end?: boolean // exact-path matching, so parent paths don't stay highlighted
}

interface NavSection {
  heading?: string
  roles?: Role[]
  items: NavItem[]
}

const NAV: NavSection[] = [
  {
    items: [
      { to: '/chat', label: 'Chat' },
      { to: '/conversations', label: 'Conversations' },
    ],
  },
  {
    heading: 'Storefront',
    // The cart is a drawer on the storefront itself (its button lives there), not a page.
    items: [
      { to: '/storefront', label: 'Products', end: true },
      { to: '/storefront/orders', label: 'Orders' },
    ],
  },
  {
    heading: 'Manage',
    roles: ['merchant', 'admin'],
    items: [
      { to: '/products', label: 'Products', end: true },
      { to: '/products/orders', label: 'Orders' },
    ],
  },
  {
    items: [
      { to: '/dashboard', label: 'Dashboard', roles: ['merchant', 'admin'] },
      { to: '/users', label: 'Users', roles: ['admin'] },
    ],
  },
]

const initials = (name: string) =>
  name
    .split(' ')
    .filter(Boolean)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

/** Light/dark switch: flips the html[data-theme] attribute and remembers the choice. */
export function ThemeToggle() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? 'light')

  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    localStorage.setItem('loom.theme', next)
    setTheme(next)
  }

  return (
    <button
      className="icon-btn theme-toggle"
      onClick={toggle}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {theme === 'dark' ? (
        // sun
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
          <circle cx="12" cy="12" r="5" />
          <line x1="12" y1="1" x2="12" y2="3" />
          <line x1="12" y1="21" x2="12" y2="23" />
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
          <line x1="1" y1="12" x2="3" y2="12" />
          <line x1="21" y1="12" x2="23" y2="12" />
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
        </svg>
      ) : (
        // moon
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
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
    </button>
  )
}

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
  // Off-canvas sidebar state for narrow screens (the burger button toggles it;
  // on desktop widths the CSS ignores it and the sidebar is always visible).
  const [navOpen, setNavOpen] = useState(false)
  const { user, ready, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const activeChat = new URLSearchParams(location.search).get('c')

  // Navigating anywhere closes the mobile sidebar.
  useEffect(() => {
    setNavOpen(false)
  }, [location])

  // Keep the sidebar's recent-chats list fresh: refetch on sign-in and on every
  // navigation (sending a first message updates the URL to ?c=<id>, landing here too).
  useEffect(() => {
    if (!user) {
      setRecent([])
      return
    }
    // Same staleness guard as the health effect below: this refires on every
    // navigation, so two quick moves can otherwise apply in arrival order and leave
    // the sidebar one navigation behind.
    let cancelled = false
    api
      .conversations(12)
      .then((res) => !cancelled && setRecent(res.conversations))
      .catch(() => {})
    return () => {
      cancelled = true
    }
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
        <button
          className="burger"
          title={navOpen ? 'Hide the menu' : 'Show the menu'}
          onClick={() => setNavOpen((o) => !o)}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
      )}
      {user && navOpen && <div className="drawer-overlay nav-overlay" onClick={() => setNavOpen(false)} />}
      {user && (
        <aside className={`sidebar ${navOpen ? 'open' : ''}`}>
          <div className="brand">
            Loom
            <span className={`health-dot ${health}`} title={`API: ${health}`} />
          </div>
          <nav>
            {NAV.filter(({ roles }) => !roles || roles.includes(user.role)).map((section, i) => {
              const items = section.items.filter(
                ({ roles }) => !roles || roles.includes(user.role),
              )
              if (items.length === 0) return null
              return (
                <div key={section.heading ?? i} className="nav-section">
                  {section.heading && <div className="nav-heading">{section.heading}</div>}
                  {items.map(({ to, label, end }) => (
                    <NavLink
                      key={to}
                      to={to}
                      end={end}
                      className={({ isActive }) =>
                        `${section.heading ? 'sub' : ''} ${isActive ? 'active' : ''}`.trim()
                      }
                    >
                      {label}
                    </NavLink>
                  ))}
                </div>
              )
            })}
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
              {user.role !== 'client' && <span className="badge accent">{user.role}</span>}
              <ThemeToggle />
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
            <div>multi-agent system</div>
          </div>
        </aside>
      )}
      <main className="content">
        <Suspense fallback={<p className="muted">loading…</p>}>
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
          <Route
            path="/dashboard"
            element={
              <RequireAuth roles={['merchant', 'admin']}>
                {user?.role === 'admin' ? <Dashboard /> : <ShopDashboard />}
              </RequireAuth>
            }
          />
          <Route
            path="/products"
            element={
              <RequireAuth roles={['merchant', 'admin']}>
                <Products tab="catalog" />
              </RequireAuth>
            }
          />
          <Route
            path="/products/orders"
            element={
              <RequireAuth roles={['merchant', 'admin']}>
                <Products tab="orders" />
              </RequireAuth>
            }
          />
          <Route
            path="/users"
            element={
              <RequireAuth roles={['admin']}>
                <Users />
              </RequireAuth>
            }
          />
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
          <Route path="/storefront" element={<Storefront tab="products" />} />
          {/* The cart page became a drawer on the storefront — keep old links working. */}
          <Route path="/storefront/cart" element={<Navigate to="/storefront" replace />} />
          <Route path="/storefront/orders" element={<Storefront tab="orders" />} />
          {/* Anything else renders an empty main area otherwise, which reads as a
              broken page rather than a wrong address. */}
          <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
