import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, fmtDate } from '../api'
import { useAuth } from '../auth'
import type { Coupon, Order, Product } from '../types'

type Tab = 'products' | 'orders' | 'coupons'

export default function Storefront() {
  const { user } = useAuth()
  const [tab, setTab] = useState<Tab>('products')
  const [products, setProducts] = useState<Product[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [coupons, setCoupons] = useState<Coupon[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Orders are per-account, so they're only fetched (and only visible) signed in.
    Promise.all([api.products(), api.coupons(), user ? api.orders() : Promise.resolve(null)])
      .then(([p, c, o]) => {
        setProducts(p.products)
        setCoupons(c.coupons)
        setOrders(o?.orders ?? [])
        setLoaded(true)
      })
      .catch((err) => setError(String(err)))
  }, [user])

  return (
    <>
      <h1 className="page-title">Storefront</h1>
      <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
        The demo shop's data — what the agents' tools read and write.
      </p>
      {error && <div className="error-banner">Could not load shop data. {error}</div>}

      <div className="tabs">
        {(['products', 'orders', 'coupons'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {loaded && tab === 'products' && (
        <div className="product-grid">
          {products.map((p) => (
            <div key={p.id} className="card product-card">
              <div className="name">{p.name}</div>
              <span className="badge">{p.category}</span>
              {p.deal && <span className="badge ok"> {p.deal}</span>}
              <div className="desc">{p.description}</div>
              <div className="price-row">
                <strong>${p.price.toFixed(2)}</strong>
                <span className="muted">★ {p.rating.toFixed(1)}</span>
                <span className={`badge ${p.in_stock ? 'ok' : 'bad'}`}>
                  {p.in_stock ? 'in stock' : 'out of stock'}
                </span>
              </div>
            </div>
          ))}
          {products.length === 0 && (
            <p className="muted">no products — run `python -m demo.shopping_assistant.seed`</p>
          )}
        </div>
      )}

      {loaded && tab === 'orders' && !user && (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            Orders are private to each account — <Link to="/login">sign in</Link> to see yours.
          </p>
        </div>
      )}

      {loaded && tab === 'orders' && user && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>order</th>
                <th>email</th>
                <th>status</th>
                <th>carrier</th>
                <th>tracking</th>
                <th>ETA</th>
                <th>placed</th>
                <th>total</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td>{o.order_id}</td>
                  <td>{o.email}</td>
                  <td>
                    <span className="badge">{o.status}</span>
                  </td>
                  <td>{o.carrier ?? '—'}</td>
                  <td>{o.tracking_number ?? '—'}</td>
                  <td>{o.estimated_delivery ?? '—'}</td>
                  <td>{fmtDate(o.placed_on)}</td>
                  <td>${o.total.toFixed(2)}</td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    no orders on your account
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {loaded && tab === 'coupons' && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>code</th>
                <th>discount</th>
                <th>scope</th>
                <th>min subtotal</th>
              </tr>
            </thead>
            <tbody>
              {coupons.map((c) => (
                <tr key={c.code}>
                  <td>
                    <code>{c.code}</code>
                  </td>
                  <td>{c.discount_pct}%</td>
                  <td>{c.product_id ?? 'whole cart'}</td>
                  <td>{c.min_subtotal != null ? `$${c.min_subtotal.toFixed(2)}` : '—'}</td>
                </tr>
              ))}
              {coupons.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    no coupons
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
