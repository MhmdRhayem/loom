import type {
  AgentAnalyticsResponse,
  AgentsResponse,
  ChatRequest,
  ChatResponse,
  ConversationDetailResponse,
  ConversationListResponse,
  Coupon,
  FeedbackRequest,
  FeedbackResponse,
  HealthResponse,
  MemoryAnalyticsResponse,
  Order,
  OverviewResponse,
  Product,
  RoutingAnalyticsResponse,
  TimeseriesResponse,
} from './types'

// The backend origin. CORS on the API side already allows the Vite dev server.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) throw new Error(`${init?.method ?? 'GET'} ${path} failed (${res.status})`)
  return (await res.json()) as T
}

const get = <T,>(path: string) => request<T>(path)

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const api = {
  health: () => get<HealthResponse>('/health'),
  agents: () => get<AgentsResponse>('/agents'),
  chat: (req: ChatRequest) => post<ChatResponse>('/chat', req),
  feedback: (req: FeedbackRequest) => post<FeedbackResponse>('/feedback', req),

  overview: () => get<OverviewResponse>('/analytics/overview'),
  agentAnalytics: () => get<AgentAnalyticsResponse>('/analytics/agents'),
  routing: () => get<RoutingAnalyticsResponse>('/analytics/routing'),
  timeseries: (bucket: 'day' | 'hour' = 'day', limit = 30) =>
    get<TimeseriesResponse>(`/analytics/timeseries?bucket=${bucket}&limit=${limit}`),
  memory: (ownerId?: string) =>
    get<MemoryAnalyticsResponse>(
      `/analytics/memory${ownerId ? `?owner_id=${encodeURIComponent(ownerId)}` : ''}`,
    ),

  conversations: (limit = 50) => get<ConversationListResponse>(`/conversations?limit=${limit}`),
  conversation: (id: string) =>
    get<ConversationDetailResponse>(`/conversations/${encodeURIComponent(id)}`),

  products: () => get<{ products: Product[] }>('/shop/products'),
  orders: () => get<{ orders: Order[] }>('/shop/orders'),
  coupons: () => get<{ coupons: Coupon[] }>('/shop/coupons'),
}

// --- tiny shared formatters (analytics numbers are nullable end to end) ---

export const fmtScore = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

export const fmtPct = (v: number | null | undefined) =>
  v == null ? '—' : `${(v * 100).toFixed(0)}%`

export const fmtMs = (v: number | null | undefined) =>
  v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : '—'
