import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, fmtDate } from '../api'
import type { ConversationSummary } from '../types'

export default function Conversations() {
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .conversations()
      .then((res) => setConversations(res.conversations))
      .catch((err) => setError(String(err)))
  }, [])

  return (
    <>
      <h1 className="page-title">Conversations</h1>
      <p className="muted" style={{ marginTop: -10, marginBottom: 16 }}>
        Your conversations with the assistant — other accounts can't see them.
      </p>
      {error && <div className="error-banner">Could not load conversations. {error}</div>}
      {!error && conversations == null && <p className="muted">Loading…</p>}
      {conversations != null && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>owner</th>
                <th>status</th>
                <th>turns</th>
                <th>started</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {conversations.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link className="row-link" to={`/conversations/${c.id}`}>
                      {c.id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td>{c.owner_id}</td>
                  <td>
                    <span className="badge">{c.status}</span>
                  </td>
                  <td>{c.total_turns}</td>
                  <td>{fmtDate(c.created_at)}</td>
                  <td>
                    <Link className="row-link" to={`/chat?c=${c.id}`}>
                      continue →
                    </Link>
                  </td>
                </tr>
              ))}
              {conversations.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    no conversations yet — try the Chat page
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
