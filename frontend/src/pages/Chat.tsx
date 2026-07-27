import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { api, fmtPct, fmtScore, type StageEvent } from '../api'
import { useAuth } from '../auth'
import type { AgentInfo, ChatResponse } from '../types'

/** Human label for what the pipeline is doing next, given the stage that just finished. */
const stageLabel = (evt: StageEvent): string => {
  switch (evt.node) {
    case 'load_memory':
      return 'choosing the right specialist…'
    case 'route':
      return evt.agents && evt.agents.length > 0
        ? `asking ${evt.agents.join(' + ')}…`
        : 'asking a specialist…'
    case 'execute_agents':
      return 'reviewing the answer…'
    case 'revise':
      return 'improving the answer…'
    case 'evaluate':
      return 'wrapping up…'
    default:
      return 'thinking…'
  }
}

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

// Extra chips for merchants: their shop_manager agent proposes catalog changes.
const MERCHANT_SUGGESTIONS = [
  'Add a red summer dress for $45 to my shop',
  'Which of my products are out of stock?',
]

const fmtTime = (d: Date) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** Tool-call args as a compact one-line preview; the full JSON lives in the tooltip. */
const fmtArgs = (args?: Record<string, unknown>) => {
  if (!args || Object.keys(args).length === 0) return ''
  const s = JSON.stringify(args).slice(1, -1) // drop the outer braces
  return s.length > 44 ? `${s.slice(0, 41)}…` : s
}

/** The routing/eval trace behind one assistant reply, collapsed by default. */
function Trace({ meta }: { meta: ChatResponse }) {
  return (
    <details className="trace">
      <summary>how this answer was produced</summary>
      <div className="trace-body">
        <div className="trace-row">
          <span className="trace-label">routing</span>
          <span className="trace-content">
            {meta.current_agents.map((a) => (
              <span key={a} className="badge accent">
                {a}
              </span>
            ))}
            {meta.query_category && <span className="badge">{meta.query_category}</span>}
            {meta.routing_confidence != null && (
              <span className="badge">{fmtPct(meta.routing_confidence)} confidence</span>
            )}
            {meta.routing_reason && <span className="trace-note">{meta.routing_reason}</span>}
          </span>
        </div>
        {meta.agent_runs.map((run) => (
          <div key={run.agent} className="trace-row">
            <span className="trace-label">agent</span>
            <span className="trace-content">
              <span className="badge accent">{run.agent}</span>
              {run.tokens > 0 && <span className="muted">{run.tokens} tokens</span>}
              {run.tool_calls.length === 0 && (
                <span className="muted">answered directly — no tools</span>
              )}
              {run.tool_calls.map((tc, i) => (
                <code key={i} className="tool-chip" title={JSON.stringify(tc.args ?? {}, null, 2)}>
                  {String(tc.name ?? 'tool')}({fmtArgs(tc.args)})
                </code>
              ))}
            </span>
          </div>
        ))}
        {meta.eval && (
          <div className="trace-row">
            <span className="trace-label">eval</span>
            <span className="trace-content">
              {meta.eval.pass != null && (
                <span className={`badge ${meta.eval.pass ? 'ok' : 'bad'}`}>
                  {meta.eval.pass ? 'pass' : 'fail'}
                </span>
              )}
              {meta.eval.score != null && (
                <span className="badge">score {fmtScore(meta.eval.score)}</span>
              )}
              {meta.eval.stage && <span className="muted">{meta.eval.stage}</span>}
              {meta.eval.feedback && <span className="trace-note">“{meta.eval.feedback}”</span>}
            </span>
          </div>
        )}
        {meta.retry_count > 0 && (
          <div className="trace-row">
            <span className="trace-label">retries</span>
            <span className="trace-content">
              <span className="badge bad">×{meta.retry_count}</span>
            </span>
          </div>
        )}
      </div>
    </details>
  )
}

function Welcome({
  agents,
  merchant,
  onPick,
}: {
  agents: AgentInfo[]
  merchant: boolean
  onPick: (s: string) => void
}) {
  const suggestions = merchant ? [...SUGGESTIONS, ...MERCHANT_SUGGESTIONS] : SUGGESTIONS
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
        {suggestions.map((s) => (
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
  const [stage, setStage] = useState<string | null>(null)
  // The reply streaming in, token by token; the final response replaces it.
  const [draft, setDraft] = useState('')
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

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
    // Two quick sidebar clicks put two fetches in flight; without this flag whichever
    // resolves last wins, so A's transcript can end up on screen while the URL and the
    // highlight say B — and the next send() posts into A.
    let stale = false
    api
      .conversation(resumeId)
      .then((detail) => {
        if (stale) return
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
        if (stale) return
        setMessages([
          { role: 'error', text: 'Could not load that conversation.', at: new Date() },
        ])
      })
    return () => {
      stale = true
    }
  }, [resumeId])

  // Follow the tail as the answer streams, but stop fighting the user: if they have
  // scrolled up to reread something, the next token must not yank them back down.
  useEffect(() => {
    const log = logRef.current
    if (!log) return
    const distanceFromBottom = log.scrollHeight - log.scrollTop - log.clientHeight
    if (distanceFromBottom > 80) return
    log.scrollTo({ top: log.scrollHeight, behavior: 'smooth' })
  }, [messages, sending, draft])

  const send = async (text?: string) => {
    const message = (text ?? input).trim()
    if (!message || sending) return
    setInput('')
    setSending(true)
    setMessages((m) => [...m, { role: 'user', text: message, at: new Date() }])
    abortRef.current = new AbortController()
    try {
      // The backend takes the owner from the Bearer token, so no owner_id is sent.
      // Streamed: stage events narrate the pipeline, token events type the answer
      // out live, and the final payload replaces the draft (it is authoritative —
      // e.g. after a critic-forced revision, signalled by a `revise` stage).
      const res = await api.chatStream(
        { message, conversation_id: conversationId },
        (evt) => {
          setStage(stageLabel(evt))
          if (evt.node === 'revise') setDraft('')
        },
        (token) => setDraft((d) => d + token),
        abortRef.current.signal,
      )
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
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages((m) => [...m, { role: 'error', text: 'Response stopped.', at: new Date() }])
      } else {
        setMessages((m) => [...m, { role: 'error', text: String(err), at: new Date() }])
      }
    } finally {
      abortRef.current = null
      setSending(false)
      setStage(null)
      setDraft('')
      inputRef.current?.focus()
    }
  }

  const stop = () => abortRef.current?.abort()

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
        {messages.length === 0 && (
          <Welcome agents={agents} merchant={user?.role === 'merchant'} onPick={(s) => send(s)} />
        )}
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
        {sending && draft && (
          // polite, not assertive: the answer grows a token at a time, and an assertive
          // region would interrupt the screen reader on every one.
          <div className="msg-row assistant" aria-live="polite">
            <div className="msg-sender">assistant</div>
            <div className="msg assistant">
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
              </div>
              <span className="stream-cursor" />
            </div>
          </div>
        )}
        {sending && !draft && (
          <div className="msg-row assistant">
            <div className="msg-sender">assistant</div>
            <div className="msg assistant typing">
              <span />
              <span />
              <span />
              <em className="stage">{stage ?? 'thinking…'}</em>
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
        {sending ? (
          <button className="ghost" onClick={stop} title="Stop the response">
            Stop
          </button>
        ) : (
          <button className="primary" onClick={() => send()} disabled={!input.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  )
}
