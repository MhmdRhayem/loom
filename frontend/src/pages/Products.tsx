import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { API_BASE, api } from '../api'
import { TrashIcon } from '../App'
import { useAuth } from '../auth'
import type { Product, ProductChange, ProductPayload, ShopOrder, ShopStats } from '../types'

type Tab = 'catalog' | 'orders'

interface ProductForm {
  name: string
  price: string
  category: string
  description: string
  deal: string
  in_stock: boolean
  shop: string
}

const EMPTY_FORM: ProductForm = {
  name: '',
  price: '',
  category: '',
  description: '',
  deal: '',
  in_stock: true,
  shop: '',
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

/** Shop management for merchants (their own shop) and admins (all shops); the
 *  sidebar's Manage sublist picks the view: catalog or customer orders. */
export default function Products({ tab }: { tab: Tab }) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [stats, setStats] = useState<ShopStats | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [changes, setChanges] = useState<ProductChange[]>([])
  const [orders, setOrders] = useState<ShopOrder[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [form, setForm] = useState<ProductForm>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(
    () =>
      Promise.all([api.manageStats(), api.manageProducts(), api.changes(), api.manageOrders()])
        .then(([s, p, c, o]) => {
          setStats(s)
          setProducts(p.products)
          setChanges(c.changes)
          setOrders(o.orders)
          setLoaded(true)
          setError(null)
        })
        .catch((err) => setError(String(err))),
    [],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  const shops = [...new Set(products.map((p) => p.shop))].sort()

  const startEdit = (p: Product) => {
    setEditingId(p.id)
    setNotice(null)
    setForm({
      name: p.name,
      price: String(p.price),
      category: p.category,
      description: p.description,
      deal: p.deal ?? '',
      in_stock: p.in_stock,
      shop: p.shop,
    })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    const body: ProductPayload = {
      name: form.name.trim(),
      price: Number(form.price),
      category: form.category.trim(),
      description: form.description.trim(),
      deal: form.deal.trim() || null,
      in_stock: form.in_stock,
    }
    try {
      if (editingId) {
        await api.updateProduct(editingId, body)
        setNotice(`Updated ${editingId}.`)
      } else {
        // shop is admin-only: a merchant is pinned to their own shop server-side.
        const created = await api.createProduct(isAdmin ? { ...body, shop: form.shop } : body)
        setNotice(`Added ${created.name} (${created.id}).`)
      }
      cancelEdit()
      await refresh()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (p: Product) => {
    if (!window.confirm(`Delete ${p.name} (${p.id})? This cannot be undone.`)) return
    setError(null)
    try {
      await api.deleteProduct(p.id)
      if (editingId === p.id) cancelEdit()
      setNotice(`Deleted ${p.id}.`)
      await refresh()
    } catch (err) {
      setError(String(err))
    }
  }

  const decide = async (c: ProductChange, verdict: 'approved' | 'rejected') => {
    setError(null)
    try {
      if (verdict === 'approved') await api.approveChange(c.change_id)
      else await api.rejectChange(c.change_id)
      setNotice(`${c.change_id} ${verdict}.`)
      await refresh()
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <>
      <h1 className="page-title">{tab === 'catalog' ? 'Products' : 'Orders'}</h1>
      <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
        {tab === 'orders'
          ? isAdmin
            ? 'Customer orders across all shops.'
            : `Customer orders containing ${user?.shop ?? 'your shop'}'s products.`
          : isAdmin
            ? 'Managing the whole catalog across all shops.'
            : `Managing ${user?.shop ?? 'your shop'} — changes apply to the live storefront.`}
      </p>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="success-banner" style={{ marginBottom: 16 }}>{notice}</div>}
      {!loaded && !error && <p className="muted">Loading…</p>}

      {loaded && tab === 'catalog' && stats && (
        <div className="grid stats" style={{ marginBottom: 16 }}>
          <Stat label="products" value={stats.products} />
          <Stat label="in stock" value={stats.in_stock} />
          <Stat label="avg rating" value={stats.avg_rating == null ? '—' : stats.avg_rating.toFixed(1)} />
          <Stat label="avg price" value={stats.avg_price == null ? '—' : `$${stats.avg_price.toFixed(2)}`} />
        </div>
      )}

      {loaded && tab === 'catalog' && changes.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3>Pending AI changes</h3>
          <p className="muted" style={{ margin: '4px 0 10px' }}>
            Proposed by the shop assistant in chat — nothing touches the catalog until you approve
            it here.
          </p>
          {changes.map((c) => (
            <div key={c.change_id} className="change-row">
              <div className="change-head">
                <span
                  className={`badge ${
                    c.action === 'delete' ? 'bad' : c.action === 'create' ? 'ok' : 'accent'
                  }`}
                >
                  {c.action}
                </span>
                <code>{c.product_id ?? 'new product'}</code>
                {isAdmin && <span className="badge">{c.shop}</span>}
                <span className="muted">
                  {c.change_id} · {c.created_on}
                </span>
                <span className="change-actions">
                  <button className="primary" onClick={() => decide(c, 'approved')}>
                    Approve
                  </button>
                  <button className="ghost" onClick={() => decide(c, 'rejected')}>
                    Reject
                  </button>
                </span>
              </div>
              {Object.keys(c.payload).length > 0 && (
                <div className="change-payload">
                  {Object.entries(c.payload).map(([k, v]) => (
                    <span key={k}>
                      <em>{k}</em>
                      {String(v ?? '—')}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {loaded && tab === 'catalog' && (
        <>
          <form className="card" style={{ marginBottom: 16 }} onSubmit={submit}>
            <h3>{editingId ? `Edit ${editingId}` : 'Add product'}</h3>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="pf-name">Name</label>
                <input
                  id="pf-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="pf-price">Price ($)</label>
                <input
                  id="pf-price"
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={form.price}
                  onChange={(e) => setForm({ ...form, price: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="pf-category">Category</label>
                <input
                  id="pf-category"
                  placeholder="e.g. dresses"
                  value={form.category}
                  onChange={(e) => setForm({ ...form, category: e.target.value })}
                />
              </div>
              {isAdmin && !editingId && (
                <div className="field">
                  <label htmlFor="pf-shop">Shop</label>
                  <select
                    id="pf-shop"
                    value={form.shop}
                    onChange={(e) => setForm({ ...form, shop: e.target.value })}
                    required
                  >
                    <option value="">choose…</option>
                    {shops.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="field form-wide">
                <label htmlFor="pf-desc">Description</label>
                <input
                  id="pf-desc"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="pf-deal">Deal (optional)</label>
                <input
                  id="pf-deal"
                  placeholder="e.g. 10% off this week"
                  value={form.deal}
                  onChange={(e) => setForm({ ...form, deal: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="pf-stock">Stock</label>
                <label className="filter-stock" style={{ padding: '9px 0' }}>
                  <input
                    id="pf-stock"
                    type="checkbox"
                    checked={form.in_stock}
                    onChange={(e) => setForm({ ...form, in_stock: e.target.checked })}
                  />
                  in stock
                </label>
              </div>
            </div>
            <div className="form-actions">
              <button className="primary" type="submit" disabled={busy || !form.name || !form.price}>
                {busy ? '…' : editingId ? 'Save changes' : 'Add product'}
              </button>
              {editingId && (
                <button className="ghost" type="button" onClick={cancelEdit}>
                  Cancel
                </button>
              )}
            </div>
          </form>

          <div className="card">
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th>id</th>
                  <th>name</th>
                  <th>category</th>
                  {isAdmin && <th>shop</th>}
                  <th>price</th>
                  <th>rating</th>
                  <th>stock</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={p.id}>
                    <td>
                      {p.image_url && (
                        <img className="product-thumb" src={`${API_BASE}${p.image_url}`} alt="" />
                      )}
                    </td>
                    <td>
                      <code>{p.id}</code>
                    </td>
                    <td>
                      {p.name}
                      {p.deal && (
                        <span className="badge ok" style={{ marginLeft: 6 }}>
                          {p.deal}
                        </span>
                      )}
                    </td>
                    <td>{p.category}</td>
                    {isAdmin && <td>{p.shop}</td>}
                    <td>${p.price.toFixed(2)}</td>
                    <td>★ {p.rating.toFixed(1)}</td>
                    <td>
                      <span className={`badge ${p.in_stock ? 'ok' : 'bad'}`}>
                        {p.in_stock ? 'in stock' : 'out'}
                      </span>
                    </td>
                    <td className="row-actions">
                      <button className="ghost" onClick={() => startEdit(p)}>
                        Edit
                      </button>
                      <button className="icon-btn" title="Delete product" onClick={() => remove(p)}>
                        <TrashIcon />
                      </button>
                    </td>
                  </tr>
                ))}
                {products.length === 0 && (
                  <tr>
                    <td colSpan={isAdmin ? 9 : 8} className="muted">
                      No products yet — add the first one above.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {loaded && tab === 'orders' && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>order</th>
                <th>date</th>
                <th>status</th>
                {isAdmin && <th>customer</th>}
                <th>{isAdmin ? 'items' : 'your items'}</th>
                <th style={{ textAlign: 'right' }}>{isAdmin ? 'items total' : 'your total'}</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td>
                    <code>{o.order_id}</code>
                  </td>
                  <td>{o.placed_on}</td>
                  <td>
                    <span className="badge">{o.status}</span>
                  </td>
                  {isAdmin && <td>{o.email}</td>}
                  <td>
                    {o.items
                      .map((i) => `${i.qty}× ${i.name} ($${i.unit_price.toFixed(2)})`)
                      .join(', ')}
                  </td>
                  <td style={{ textAlign: 'right' }}>${o.items_total.toFixed(2)}</td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr>
                  <td colSpan={isAdmin ? 6 : 5} className="muted">
                    No customer orders contain your products yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
