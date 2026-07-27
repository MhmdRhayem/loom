import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { API_BASE, api, fmtDate } from '../api'
import { TrashIcon } from '../App'
import { useAuth } from '../auth'
import type { CartResponse, CheckoutResponse, Order, Product } from '../types'

type Tab = 'products' | 'orders'

const TITLES: Record<Tab, string> = { products: 'Products', orders: 'Orders' }

function CartIcon({ size = 16 }: { size?: number }) {
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
      <circle cx="9" cy="21" r="1" />
      <circle cx="20" cy="21" r="1" />
      <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6" />
    </svg>
  )
}

/** One storefront view per route; the cart is a slide-over drawer, not a page. */
export default function Storefront({ tab }: { tab: Tab }) {
  const { user } = useAuth()
  const [products, setProducts] = useState<Product[]>([])
  const [shops, setShops] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [cart, setCart] = useState<CartResponse | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const [checkoutResult, setCheckoutResult] = useState<CheckoutResponse | null>(null)
  // Promo code applied at checkout. The assistant can quote one via price_api, so the
  // storefront needs somewhere to actually redeem it.
  const [coupon, setCoupon] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [expandedOrder, setExpandedOrder] = useState<string | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  // A short "added to cart" confirmation, replaced (not stacked) by rapid adds.
  const showToast = (text: string) => {
    setToast(text)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 2200)
  }
  useEffect(() => () => window.clearTimeout(toastTimer.current), [])

  // catalog filters (applied client-side — the catalog is small and already loaded)
  const [query, setQuery] = useState('')
  const [shop, setShop] = useState('')
  const [category, setCategory] = useState('')
  const [maxPrice, setMaxPrice] = useState('')
  const [inStockOnly, setInStockOnly] = useState(false)

  useEffect(() => {
    // Cart and orders are per-account, so they're only fetched (and visible) signed in.
    Promise.all([
      api.products(),
      user ? api.orders() : Promise.resolve(null),
      user ? api.cart() : Promise.resolve(null),
    ])
      .then(([p, o, k]) => {
        setProducts(p.products)
        setShops(p.shops)
        setCategories(p.categories)
        setOrders(o?.orders ?? [])
        setCart(k)
        setLoaded(true)
      })
      .catch((err) => setError(String(err)))
  }, [user])

  // The drawer closes on Escape, like a dialog.
  useEffect(() => {
    if (!cartOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setCartOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cartOpen])

  const visible = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
    const cap = maxPrice === '' ? null : Number(maxPrice)
    return products.filter((p) => {
      if (shop && p.shop !== shop) return false
      if (category && p.category !== category) return false
      if (cap != null && !Number.isNaN(cap) && p.price > cap) return false
      if (inStockOnly && !p.in_stock) return false
      if (terms.length > 0) {
        const blob = `${p.name} ${p.category} ${p.shop} ${p.description}`.toLowerCase()
        if (!terms.every((t) => blob.includes(t))) return false
      }
      return true
    })
  }, [products, query, shop, category, maxPrice, inStockOnly])

  const resetFilters = () => {
    setQuery('')
    setShop('')
    setCategory('')
    setMaxPrice('')
    setInStockOnly(false)
  }

  const filtersActive =
    query !== '' || shop !== '' || category !== '' || maxPrice !== '' || inStockOnly

  const addToCart = async (productId: string, name?: string) => {
    try {
      setCart(await api.addToCart(productId))
      setCheckoutResult(null)
      if (name) showToast(`${name} added to cart`)
    } catch (err) {
      setError(String(err))
    }
  }

  const decrementCart = async (productId: string) => {
    try {
      setCart(await api.decrementCart(productId))
      setCheckoutResult(null)
    } catch (err) {
      setError(String(err))
    }
  }

  const removeFromCart = async (productId: string) => {
    try {
      setCart(await api.removeFromCart(productId))
      setCheckoutResult(null)
    } catch (err) {
      setError(String(err))
    }
  }

  const checkout = async (newCard = false) => {
    try {
      const res = await api.checkout(newCard, coupon.trim())
      setCheckoutResult(res)
      if (res.placed) {
        setCoupon('')
        setCart(await api.cart())
        setOrders((await api.orders()).orders)
      }
    } catch (err) {
      setError(String(err))
    }
  }

  const cartLines = cart?.cart ?? []
  const cartCount = cartLines.reduce((n, line) => n + line.qty, 0)

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">{TITLES[tab]}</h1>
        {user && (
          <button className="ghost cart-btn" onClick={() => setCartOpen(true)} title="Open cart">
            <CartIcon />
            Cart
            {cartCount > 0 && <span className="cart-count">{cartCount}</span>}
          </button>
        )}
      </div>
      {tab === 'products' && (
        <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
          The demo shop's catalog — what the agents' tools read and write.
        </p>
      )}
      {error && <div className="error-banner">Could not load shop data. {error}</div>}

      {loaded && tab === 'products' && (
        <>
          <div className="filter-bar">
            <input
              className="filter-search"
              placeholder="Search products…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select value={shop} onChange={(e) => setShop(e.target.value)}>
              <option value="">all shops</option>
              {shops.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">all categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <input
              className="filter-price"
              type="number"
              min="0"
              placeholder="max $"
              value={maxPrice}
              onChange={(e) => setMaxPrice(e.target.value)}
            />
            <label className="filter-stock">
              <input
                type="checkbox"
                checked={inStockOnly}
                onChange={(e) => setInStockOnly(e.target.checked)}
              />
              in stock
            </label>
            {filtersActive && (
              <button className="ghost" onClick={resetFilters}>
                clear
              </button>
            )}
            <span className="muted filter-count">
              {visible.length} of {products.length}
            </span>
          </div>

          <div className="product-grid">
            {visible.map((p) => (
              <div key={p.id} className="card product-card">
                {p.image_url && (
                  <img
                    className="product-img"
                    src={`${API_BASE}${p.image_url}`}
                    alt={p.name}
                    loading="lazy"
                  />
                )}
                <div className="name">{p.name}</div>
                <div className="product-badges">
                  <span className="badge accent">{p.shop}</span>
                  <span className="badge">{p.category}</span>
                  {p.deal && <span className="badge ok">{p.deal}</span>}
                </div>
                <div className="desc">{p.description}</div>
                <div className="price-row">
                  <strong>${p.price.toFixed(2)}</strong>
                  <span className="muted">★ {p.rating.toFixed(1)}</span>
                  <span className={`badge ${p.in_stock ? 'ok' : 'bad'}`}>
                    {p.in_stock ? 'in stock' : 'out of stock'}
                  </span>
                </div>
                {user && (
                  <button
                    className="ghost add-to-cart"
                    disabled={!p.in_stock}
                    onClick={() => addToCart(p.id, p.name)}
                  >
                    Add to cart
                  </button>
                )}
              </div>
            ))}
            {products.length === 0 && (
              <p className="muted">no products — run `python -m demo.shopping_assistant.seed`</p>
            )}
            {products.length > 0 && visible.length === 0 && (
              <p className="muted">nothing matches these filters</p>
            )}
          </div>
        </>
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
                <Fragment key={o.order_id}>
                  <tr
                    className="order-row"
                    title="Click to see the items"
                    onClick={() =>
                      setExpandedOrder(expandedOrder === o.order_id ? null : o.order_id)
                    }
                  >
                    <td>
                      <span className={`order-chevron ${expandedOrder === o.order_id ? 'open' : ''}`}>
                        ▸
                      </span>
                      {o.order_id}
                    </td>
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
                  {expandedOrder === o.order_id && (
                    <tr className="order-items-row">
                      <td colSpan={8}>
                        {o.items.length === 0 ? (
                          <span className="muted">no line items recorded for this order</span>
                        ) : (
                          <ul className="order-items">
                            {o.items.map((line) => (
                              <li key={line.product_id}>
                                <span>
                                  {line.qty} × {line.name}
                                </span>
                                <span className="muted">
                                  ${(line.qty * line.unit_price).toFixed(2)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
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

      {user && cartOpen && (
        <>
          <div className="drawer-overlay" onClick={() => setCartOpen(false)} />
          {/* A bare <aside> is a landmark, not a dialog: without these a screen reader
              announces no context on open and keeps the whole product grid in the tab
              order behind it. Escape is already handled below. */}
          <aside className="drawer" role="dialog" aria-modal="true" aria-label="Cart">
            <div className="drawer-head">
              <h2>
                Cart{cartCount > 0 ? ` (${cartCount})` : ''}
              </h2>
              <button className="icon-btn" title="Close cart" onClick={() => setCartOpen(false)}>
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
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="drawer-body">
              {cartLines.length === 0 && !checkoutResult?.placed && (
                <p className="muted">Your cart is empty — add something from the products grid.</p>
              )}
              {cartLines.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>item</th>
                      <th>qty</th>
                      <th>price</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {cartLines.map((line) => (
                      <tr key={line.id}>
                        <td>{line.name}</td>
                        <td>
                          <span className="qty-stepper">
                            <button
                              className="qty-btn"
                              title="One less"
                              onClick={() => decrementCart(line.id)}
                            >
                              −
                            </button>
                            {line.qty}
                            <button
                              className="qty-btn"
                              title="One more"
                              onClick={() => addToCart(line.id)}
                            >
                              +
                            </button>
                          </span>
                        </td>
                        <td>${(line.qty * line.price).toFixed(2)}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            className="icon-btn"
                            title="Remove from cart"
                            onClick={() => removeFromCart(line.id)}
                          >
                            <TrashIcon />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {checkoutResult && !checkoutResult.placed && (
                <div className="error-banner" style={{ marginTop: 12, marginBottom: 0 }}>
                  {checkoutResult.error}
                  {checkoutResult.suggestion && (
                    <div className="muted">{checkoutResult.suggestion}</div>
                  )}
                  {checkoutResult.retry_with_new_card && (
                    <button className="ghost" style={{ marginTop: 8 }} onClick={() => checkout(true)}>
                      Use a different card
                    </button>
                  )}
                </div>
              )}
              {checkoutResult?.placed && (
                <div className="success-banner">
                  Order <strong>{checkoutResult.order_id}</strong> placed — $
                  {checkoutResult.total?.toFixed(2)}
                  {checkoutResult.coupon_code
                    ? ` (${checkoutResult.coupon_code}, -${checkoutResult.discount_pct}% off $${checkoutResult.subtotal?.toFixed(2)})`
                    : ''}
                  , estimated delivery {checkoutResult.estimated_delivery}. Ask the assistant
                  "where is my order {checkoutResult.order_id}?" to track it.
                </div>
              )}
            </div>

            {cartLines.length > 0 && (
              <div className="drawer-foot">
                <label className="coupon-field">
                  <span className="muted">promo code</span>
                  <input
                    value={coupon}
                    onChange={(e) => setCoupon(e.target.value)}
                    placeholder="e.g. SAVE10"
                    autoComplete="off"
                  />
                </label>
                <strong>subtotal ${cart?.subtotal.toFixed(2)}</strong>
                <button className="primary" onClick={() => checkout(false)}>
                  Checkout
                </button>
              </div>
            )}
          </aside>
        </>
      )}

      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </>
  )
}
