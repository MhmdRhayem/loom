import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api, fmtDate, fmtMs, fmtScore } from '../api'
import type { ConversationDetailResponse } from '../types'

export default function ConversationDetail() {
  const { id } = useParams<{ id: string }>()
  const [detail, setDetail] = useState<ConversationDetailResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .conversation(id)
      .then(setDetail)
      .catch((err) => setError(String(err)))
  }, [id])

  return (
    <>
      <h1 className="page-title">
        <Link className="row-link" to="/conversations">
          Conversations
        </Link>{' '}
        / {id?.slice(0, 8)}…
      </h1>
      {error && <div className="error-banner">Could not load this conversation. {error}</div>}
      {!error && detail == null && <p className="muted">Loading…</p>}
      {detail && (
        <>
          <div className="turn-head" style={{ marginBottom: 16 }}>
            <span className="badge">owner {detail.conversation.owner_id}</span>
            <span className="badge">{detail.conversation.status}</span>
            <span className="badge">{detail.conversation.total_turns} turns</span>
            <span className="muted">{fmtDate(detail.conversation.created_at)}</span>
          </div>

          {detail.turns.map((turn) => {
            const agents = detail.turn_agents.filter((a) => a.turn_number === turn.turn_number)
            return (
              <div key={turn.turn_number} className="card turn-card">
                <div className="turn-head">
                  <span className="badge accent">turn {turn.turn_number}</span>
                  {turn.agent_name && <span className="badge">{turn.agent_name}</span>}
                  {turn.query_category && <span className="badge">{turn.query_category}</span>}
                  {turn.model_tier && <span className="badge">{turn.model_tier}</span>}
                  {turn.eval_score != null && (
                    <span className="badge">eval {fmtScore(turn.eval_score)}</span>
                  )}
                  {turn.retry_count > 0 && <span className="badge bad">retries {turn.retry_count}</span>}
                  <span className="muted">{fmtMs(turn.latency_ms)}</span>
                </div>
                <div className="turn-q">{turn.user_message}</div>
                <div className="turn-a">{turn.agent_response ?? <span className="muted">—</span>}</div>
                {agents.length > 0 && (
                  <div className="msg-meta">
                    {agents.map((a) => (
                      <span
                        key={a.agent_name}
                        className={`badge ${a.eval_pass == null ? '' : a.eval_pass ? 'ok' : 'bad'}`}
                      >
                        {a.agent_name}
                        {a.eval_score != null && ` · ${fmtScore(a.eval_score)}`}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </>
      )}
    </>
  )
}
