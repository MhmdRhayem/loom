import { useEffect, useState } from 'react'

import { api } from '../api'
import { TrashIcon } from '../App'
import { useAuth } from '../auth'
import type { AccountRow, Role } from '../types'

const ROLES: Role[] = ['client', 'merchant', 'admin']

/** Admin-only user management: change roles, assign a merchant's shop, delete accounts. */
export default function Users() {
  const { user } = useAuth()
  const [rows, setRows] = useState<AccountRow[] | null>(null)
  const [shops, setShops] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Unsaved per-row edits, keyed by email; a row without an entry is untouched.
  const [edits, setEdits] = useState<Record<string, { role: Role; shop: string }>>({})

  const load = () =>
    Promise.all([api.users(), api.products()])
      .then(([u, p]) => {
        setRows(u.users)
        setShops(p.shops)
        setEdits({})
      })
      .catch((err) => setError(String(err)))

  useEffect(() => {
    load()
  }, [])

  const edit = (row: AccountRow, patch: Partial<{ role: Role; shop: string }>) => {
    setNotice(null)
    setEdits((e) => {
      const current = e[row.email] ?? { role: row.role, shop: row.shop ?? '' }
      return { ...e, [row.email]: { ...current, ...patch } }
    })
  }

  const save = async (row: AccountRow) => {
    const pending = edits[row.email]
    if (!pending) return
    setError(null)
    try {
      await api.updateUser(row.email, {
        role: pending.role,
        shop: pending.role === 'merchant' ? pending.shop : null,
      })
      setNotice(`Updated ${row.email}.`)
      await load()
    } catch (err) {
      setError(String(err))
    }
  }

  const remove = async (row: AccountRow) => {
    if (!window.confirm(`Delete the account ${row.email}? This cannot be undone.`)) return
    setError(null)
    try {
      await api.deleteUser(row.email)
      setNotice(`Deleted ${row.email}.`)
      await load()
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <>
      <h1 className="page-title">Users</h1>
      <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
        Every account with its role. Merchants need a shop; you cannot change or delete yourself.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner" style={{ marginBottom: 16 }}>{notice}</div>}
      {rows == null && !error && <p className="muted">Loading…</p>}

      {rows != null && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>email</th>
                <th>name</th>
                <th>role</th>
                <th>shop</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const self = row.email === user?.email
                const pending = edits[row.email]
                const role = pending?.role ?? row.role
                const shop = pending?.shop ?? row.shop ?? ''
                const dirty = pending != null && (pending.role !== row.role || pending.shop !== (row.shop ?? ''))
                return (
                  <tr key={row.email}>
                    <td>
                      {row.email}
                      {self && (
                        <span className="badge accent" style={{ marginLeft: 6 }}>
                          you
                        </span>
                      )}
                    </td>
                    <td>{row.name}</td>
                    <td>
                      <select
                        value={role}
                        disabled={self}
                        onChange={(e) => edit(row, { role: e.target.value as Role })}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        value={shop}
                        disabled={self || role !== 'merchant'}
                        onChange={(e) => edit(row, { shop: e.target.value })}
                      >
                        <option value="">—</option>
                        {shops.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="row-actions">
                      <button
                        className="ghost"
                        disabled={self || !dirty || (role === 'merchant' && !shop)}
                        onClick={() => save(row)}
                      >
                        Save
                      </button>
                      <button
                        className="icon-btn"
                        title="Delete account"
                        disabled={self}
                        onClick={() => remove(row)}
                      >
                        <TrashIcon />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
