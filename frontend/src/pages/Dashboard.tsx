import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api, fmtDate, fmtMs, fmtPct, fmtScore } from '../api'
import type {
  AgentAnalyticsResponse,
  MemoryAnalyticsResponse,
  OverviewResponse,
  RoutingAnalyticsResponse,
  TimeseriesResponse,
} from '../types'

const CHART = {
  grid: '#e4e7f0',
  text: '#667089',
  accent: '#6d5df0',
  ok: '#10b981',
  warn: '#f59e0b',
}

const TOOLTIP_STYLE = {
  backgroundColor: '#ffffff',
  border: '1px solid #d7dbe8',
  borderRadius: 8,
  color: '#1c2130',
  fontSize: 12,
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

export default function Dashboard() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null)
  const [agents, setAgents] = useState<AgentAnalyticsResponse | null>(null)
  const [routing, setRouting] = useState<RoutingAnalyticsResponse | null>(null)
  const [series, setSeries] = useState<TimeseriesResponse | null>(null)
  const [memory, setMemory] = useState<MemoryAnalyticsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.overview(), api.agentAnalytics(), api.routing(), api.timeseries(), api.memory()])
      .then(([o, a, r, t, m]) => {
        setOverview(o)
        setAgents(a)
        setRouting(r)
        setSeries(t)
        setMemory(m)
      })
      .catch((err) => setError(String(err)))
  }, [])

  if (error) {
    return (
      <>
        <h1 className="page-title">Dashboard</h1>
        <div className="error-banner">Could not load analytics — is the API running? {error}</div>
      </>
    )
  }

  if (!overview) {
    return (
      <>
        <h1 className="page-title">Dashboard</h1>
        <p className="muted">Loading…</p>
      </>
    )
  }

  return (
    <>
      <h1 className="page-title">Dashboard</h1>

      <div className="grid stats">
        <Stat label="conversations" value={overview.conversations} />
        <Stat label="turns" value={overview.turns} />
        <Stat label="avg eval score" value={fmtScore(overview.avg_eval_score)} />
        <Stat label="eval pass rate" value={fmtPct(overview.eval_pass_rate)} />
        <Stat label="avg latency" value={fmtMs(overview.avg_latency_ms)} />
        <Stat label="retry rate" value={fmtPct(overview.retry_rate)} />
        <Stat label="memories" value={overview.total_memories} />
        <Stat label="dream runs" value={overview.dream_runs} />
        <Stat
          label="total tokens"
          value={
            overview.total_tokens >= 1000
              ? `${(overview.total_tokens / 1000).toFixed(1)}k`
              : overview.total_tokens
          }
        />
      </div>

      <div className="grid charts">
        <div className="card">
          <h3>Turns per agent</h3>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={agents?.agents ?? []}>
              <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
              <XAxis dataKey="agent" stroke={CHART.text} fontSize={11} />
              <YAxis stroke={CHART.text} fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'transparent' }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="turns" fill={CHART.accent} radius={[4, 4, 0, 0]} />
              <Bar dataKey="passes" fill={CHART.ok} radius={[4, 4, 0, 0]} />
              <Bar dataKey="fails" fill={CHART.warn} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>
            Routing — queries per category{' '}
            {routing?.avg_routing_confidence != null && (
              <span className="badge">avg confidence {fmtScore(routing.avg_routing_confidence)}</span>
            )}
          </h3>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={routing?.category_distribution ?? []}>
              <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
              <XAxis dataKey="key" stroke={CHART.text} fontSize={11} />
              <YAxis stroke={CHART.text} fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'transparent' }} />
              <Bar dataKey="count" fill={CHART.accent} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>Turns over time</h3>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={series?.points ?? []}>
              <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
              <XAxis dataKey="bucket" stroke={CHART.text} fontSize={11} />
              <YAxis stroke={CHART.text} fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="turns" stroke={CHART.accent} dot={false} />
              <Line type="monotone" dataKey="avg_eval_score" stroke={CHART.ok} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>Agent performance (per category)</h3>
          <table>
            <thead>
              <tr>
                <th>agent</th>
                <th>category</th>
                <th>score</th>
                <th>successes</th>
                <th>failures</th>
              </tr>
            </thead>
            <tbody>
              {(agents?.performance ?? []).map((row) => (
                <tr key={`${row.agent}:${row.category}`}>
                  <td>{row.agent}</td>
                  <td>{row.category}</td>
                  <td>{fmtScore(row.score)}</td>
                  <td>{row.successes}</td>
                  <td>{row.failures}</td>
                </tr>
              ))}
              {(agents?.performance ?? []).length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    no learning data yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Memories per owner</h3>
          <table>
            <thead>
              <tr>
                <th>owner</th>
                <th>memories</th>
              </tr>
            </thead>
            <tbody>
              {(memory?.memory_counts ?? []).map((row) => (
                <tr key={row.owner_id}>
                  <td>{row.owner_id}</td>
                  <td>{row.count}</td>
                </tr>
              ))}
              {(memory?.memory_counts ?? []).length === 0 && (
                <tr>
                  <td colSpan={2} className="muted">
                    no memories yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Dream runs (memory consolidation)</h3>
          <table>
            <thead>
              <tr>
                <th>owner</th>
                <th>merged</th>
                <th>pruned</th>
                <th>duration</th>
                <th>started</th>
              </tr>
            </thead>
            <tbody>
              {(memory?.dream_runs ?? []).map((run, i) => (
                <tr key={i}>
                  <td>{run.owner_id}</td>
                  <td>{run.memories_merged}</td>
                  <td>{run.memories_pruned}</td>
                  <td>{fmtMs(run.duration_ms)}</td>
                  <td>{fmtDate(run.started_at)}</td>
                </tr>
              ))}
              {(memory?.dream_runs ?? []).length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    no dream runs yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
