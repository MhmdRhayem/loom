import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { api, fmtScore } from '../api'
import { useAuth } from '../auth'
import type { AgentInfo, ChatResponse } from '../types'

interface Message {
  role: 'user' | 'assistant' | 'error'
  text: string
  at: Date
  meta?: ChatResponse
  feedback?: 'up' | 'down'
}

const SUGGESTIONS = [
  'Do you have any dresses under $60?',
  'Where is my order ORD-1005?',
  'What would go well with the Classic Trench Coat?',
  'I want to return my leather boots',
]

const fmtTime = (d: Date) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** The routing/eval trace behind one assistant reply, collapsed by default. */
function Trace({ meta }: { meta: ChatResponse }) {
  return (
    <details className="trace">
      <summary>how this answer was produced</summary>
      <div className="trace-body">
        <div>
          Routed to <code>{meta.current_agents.join(', ') || '—'}</code>
          {meta.query_category && (
            <>
              {' '}
              as <code>{meta.query_category}</code>
            </>
          )}
          {meta.routing_confidence != null && <> (confidence {fmtScore(meta.routing_confidence)})</>}
        </div>
        {meta.routing_reason && <div className="muted">{meta.routing_reason}</div>}
        {meta.agent_runs.map((run) => (
          <div key={run.agent}>
            <code>{run.agent}</code>
            {run.tool_calls.length > 0 ? (
              <>
                {' '}
                called{' '}
                {run.tool_calls.map((tc, i) => (
                  <code key={i} title={JSON.stringify(tc.args ?? {}, null, 2)}>
                    {String(tc.name ?? 'tool')}({tc.args ? JSON.stringify(tc.args) : ''})
                  </code>
                ))}
              </>
            ) : (
              <span className="muted"> answered directly (no tools)</span>
            )}
          </div>
        ))}
        {meta.eval && (
          <div>
            Eval <code>{meta.eval.stage ?? '?'}</code> score {fmtScore(meta.eval.score)}{' '}
            {meta.eval.pass != null && (
              <span className={`badge ${meta.eval.pass ? 'ok' : 'bad'}`}>
                {meta.eval.pass ? 'pass' : 'fail'}
              </span>
            )}
            {meta.eval.feedback && <div className="muted">“{meta.eval.feedback}”</div>}
          </div>
        )}
        {meta.retry_count > 0 && <div className="muted">retries: {meta.retry_count}</div>}
      </div>
    </details>
  )
}

function Welcome({ agents, onPick }: { agents: AgentInfo[]; onPick: (s: string) => void }) {
  return (
    <div className="card welcome">
      <h3>Shopping assistant</h3>
      <p className="muted">
        One message, the right specialist{agents.length > 0 && ` — ${agents.length} agents on the roster`}.
        The router reads your message and hands it to whoever should answer.
      </p>
      {agents.length > 0 && (
        <div className="msg-meta">
          {agents.map((a) => (
            <span key={a.name} className="badge" title={a.description}>
              {a.name}
            </span>
          ))}
        </div>
      )}
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => onPick(s)}>
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Chat() {
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    api
      .agents()
      .then((res) => setAgents(res.agents))
      .catch(() => {})
    inputRef.current?.focus()
  }, [])

  // Arriving at (or switching to) /chat?c=<id> resumes a stored conversation: replay
  // its turns into the log and keep chatting under the same id (the backend reloads
  // the history too). loadedRef stops the effect from refetching the conversation we
  // are already in — e.g. right after send() stamps a new id into the URL.
  const resumeId = searchParams.get('c')
  const loadedRef = useRef<string | null>(null)
  useEffect(() => {
    // The ?c param went away while a conversation was open (deleted from the
    // sidebar, or the Chat nav link was clicked) — start a fresh chat.
    if (!resumeId) {
      if (loadedRef.current) {
        loadedRef.current = null
        setMessages([])
        setConversationId(null)
      }
      return
    }
    if (resumeId === loadedRef.current) return
    loadedRef.current = resumeId
    api
      .conversation(resumeId)
      .then((detail) => {
        const restored: Message[] = []
        for (const t of detail.turns) {
          restored.push({ role: 'user', text: t.user_message, at: new Date(t.created_at) })
          if (t.agent_response) {
            restored.push({ role: 'assistant', text: t.agent_response, at: new Date(t.created_at) })
          }
        }
        setConversationId(resumeId)
        setMessages(restored)
      })
      .catch(() => {
        setMessages([
          { role: 'error', text: 'Could not load that conversation.', at: new Date() },
        ])
      })
  }, [resumeId])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, sending])

  const send = async (text?: string) => {
    const message = (text ?? input).trim()
    if (!message || sending) return
    setInput('')
    setSending(true)
    setMessages((m) => [...m, { role: 'user', text: message, at: new Date() }])
    try {
      // The backend takes the owner from the Bearer token, so no owner_id is sent.
      const res = await api.chat({ message, conversation_id: conversationId })
      setConversationId(res.conversation_id)
      // Stamp the conversation into the URL so a refresh resumes it and the
      // sidebar's recent list picks it up. loadedRef stops the resume effect
      // from reloading what's already on screen.
      loadedRef.current = res.conversation_id
      setSearchParams({ c: res.conversation_id }, { replace: true })
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: res.response ?? '(no response)', at: new Date(), meta: res },
      ])
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: String(err), at: new Date() }])
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  const rate = async (index: number, rating: 'up' | 'down') => {
    if (!conversationId) return
    setMessages((m) => m.map((msg, i) => (i === index ? { ...msg, feedback: rating } : msg)))
    try {
      await api.feedback({ conversation_id: conversationId, rating })
    } catch {
      // feedback is best-effort; the backend is fail-soft about it too
    }
  }

  const reset = () => {
    setMessages([])
    setConversationId(null)
    loadedRef.current = null
    setSearchParams({}, { replace: true })
    inputRef.current?.focus()
  }

  const lastAssistant = messages.map((m) => m.role).lastIndexOf('assistant')

  return (
    <div className="chat-page">
      <div className="chat-header">
        <h1 className="page-title">Chat</h1>
        <div className="chat-controls">
          <span className="muted" title={user?.email}>
            shopping as <strong>{user?.name}</strong>
          </span>
          {conversationId && <span className="badge">conversation {conversationId.slice(0, 8)}…</span>}
          <button className="ghost" onClick={reset}>
            New conversation
          </button>
        </div>
      </div>

      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && <Welcome agents={agents} onPick={(s) => send(s)} />}
        {messages.map((msg, i) => (
          <div key={i} className={`msg-row ${msg.role}`}>
            <div className="msg-sender">
              {msg.role === 'user'
                ? 'You'
                : msg.role === 'error'
                  ? 'error'
                  : msg.meta?.current_agents.join(' + ') || 'assistant'}
              <span className="msg-time">{fmtTime(msg.at)}</span>
            </div>
            <div className={`msg ${msg.role}`}>
              {msg.role === 'assistant' ? (
                <div className="md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                </div>
              ) : (
                msg.text
              )}
              {msg.meta && (
                <>
                  <div className="msg-meta">
                    {msg.meta.query_category && (
                      <span className="badge accent">{msg.meta.query_category}</span>
                    )}
                    {msg.meta.eval?.score != null && (
                      <span className={`badge ${msg.meta.eval.pass === false ? 'bad' : 'ok'}`}>
                        eval {fmtScore(msg.meta.eval.score)}
                      </span>
                    )}
                    {msg.meta.retry_count > 0 && (
                      <span className="badge bad">retried ×{msg.meta.retry_count}</span>
                    )}
                  </div>
                  <Trace meta={msg.meta} />
                  {i === lastAssistant && (
                    <div className="feedback-row">
                      <button
                        className={msg.feedback === 'up' ? 'chosen' : ''}
                        disabled={msg.feedback != null}
                        onClick={() => rate(i, 'up')}
                        title="Good answer"
                      >
                        👍
                      </button>
                      <button
                        className={msg.feedback === 'down' ? 'chosen' : ''}
                        disabled={msg.feedback != null}
                        onClick={() => rate(i, 'down')}
                        title="Bad answer"
                      >
                        👎
                      </button>
                      {msg.feedback && <span className="muted">thanks — recorded</span>}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="msg-row assistant">
            <div className="msg-sender">assistant</div>
            <div className="msg assistant typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </div>

      <div className="chat-input">
        <textarea
          ref={inputRef}
          placeholder="Ask the shopping assistant…  (Enter to send, Shift+Enter for a new line)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          disabled={sending}
        />
        <button className="primary" onClick={() => send()} disabled={sending || !input.trim()}>
          {sending ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}
