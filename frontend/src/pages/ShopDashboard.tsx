import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../api'
import { useAuth } from '../auth'
import type { ShopSales } from '../types'

const CHART = {
  accent: '#6d5df0',
}

// Inline styles land on a real DOM node, so the theme variables resolve here directly.
// The grid/axis colours cannot come through this route (they are SVG presentation
// attributes), so those live in styles.css instead — see the recharts block there.
const TOOLTIP_STYLE = {
  backgroundColor: 'var(--bg-card)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  color: 'var(--text)',
  fontSize: 12,
}

const money = (v: number) => `$${v.toFixed(2)}`

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

/** Sales dashboard for merchants: what their shop actually sold, from placed orders. */
export default function ShopDashboard() {
  const { user } = useAuth()
  const [sales, setSales] = useState<ShopSales | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.manageSales().then(setSales).catch((err) => setError(String(err)))
  }, [])

  return (
    <>
      <h1 className="page-title">Dashboard</h1>
      <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
        Sales for {user?.shop ?? 'your shop'} — computed from customer orders that contain your
        products.
      </p>

      {error && <div className="error-banner">Could not load sales — is the API running? {error}</div>}
      {!sales && !error && <p className="muted">Loading…</p>}

      {sales && (
        <div className="grid stats" style={{ marginBottom: 16 }}>
          <Stat label="revenue" value={money(sales.revenue)} />
          <Stat label="units sold" value={sales.units} />
          <Stat label="orders" value={sales.orders} />
          <Stat
            label="avg order value"
            value={sales.avg_order_value == null ? '—' : money(sales.avg_order_value)}
          />
        </div>
      )}

      {sales && sales.orders === 0 && (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            No sales yet — orders containing your products will show up here.
          </p>
        </div>
      )}

      {sales && sales.orders > 0 && (
        <div className="grid charts">
          <div className="card">
            <h3>Revenue by day</h3>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={sales.by_day}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" fontSize={11} />
                <YAxis fontSize={11} tickFormatter={(v) => `$${v}`} />
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => money(Number(v))} />
                <Line type="monotone" dataKey="revenue" stroke={CHART.accent} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <h3>Top products by revenue</h3>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={sales.top_products}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" fontSize={11} />
                <YAxis fontSize={11} tickFormatter={(v) => `$${v}`} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  cursor={{ fill: 'transparent' }}
                  formatter={(v) => money(Number(v))}
                />
                <Bar dataKey="revenue" fill={CHART.accent} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <h3>Top products</h3>
            <table>
              <thead>
                <tr>
                  <th>id</th>
                  <th>product</th>
                  <th>units</th>
                  <th style={{ textAlign: 'right' }}>revenue</th>
                </tr>
              </thead>
              <tbody>
                {sales.top_products.map((p) => (
                  <tr key={p.product_id}>
                    <td>
                      <code>{p.product_id}</code>
                    </td>
                    <td>{p.name}</td>
                    <td>{p.units}</td>
                    <td style={{ textAlign: 'right' }}>{money(p.revenue)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Orders by status</h3>
            <table>
              <thead>
                <tr>
                  <th>status</th>
                  <th>orders</th>
                </tr>
              </thead>
              <tbody>
                {sales.status_counts.map((s) => (
                  <tr key={s.status}>
                    <td>
                      <span className="badge">{s.status}</span>
                    </td>
                    <td>{s.orders}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
