import type {
  AccountRow,
  AgentAnalyticsResponse,
  AgentsResponse,
  CartResponse,
  ChatRequest,
  ChatResponse,
  CheckoutResponse,
  ConversationDetailResponse,
  ConversationListResponse,
  FeedbackRequest,
  FeedbackResponse,
  HealthResponse,
  LoginResponse,
  MemoryAnalyticsResponse,
  Order,
  OverviewResponse,
  Product,
  ProductChange,
  ProductsResponse,
  RoutingAnalyticsResponse,
  ShopOrder,
  ShopSales,
  ShopStats,
  TimeseriesResponse,
  User,
} from './types'

// The backend origin. CORS on the API side already allows the Vite dev server.
export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'
const BASE = API_BASE

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

// The session token lives in localStorage so a refresh keeps you signed in;
// auth.tsx owns writing it, every request below reads it.
const TOKEN_KEY = 'loom.token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)

export const setToken = (token: string | null) => {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  // A 401 outside the auth endpoints means the token expired — drop it and
  // send the user back to the login page instead of failing quietly.
  if (res.status === 401 && token && !path.startsWith('/auth/')) {
    setToken(null)
    if (window.location.pathname !== '/login') window.location.assign('/login')
  }
  if (!res.ok)
    throw new ApiError(`${init?.method ?? 'GET'} ${path} failed (${res.status})`, res.status)
  return (await res.json()) as T
}

const get = <T,>(path: string) => request<T>(path)

export interface StageEvent {
  node: string
  agents?: string[]
  eval_pass?: boolean | null
}

/** POST /chat/stream — reports pipeline stages and answer tokens as they arrive,
 * resolves with the final (authoritative) reply. Abortable via `signal`. */
async function chatStream(
  req: ChatRequest,
  onStage: (evt: StageEvent) => void,
  onToken?: (text: string) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
    signal,
  })
  if (res.status === 401 && token) {
    setToken(null)
    if (window.location.pathname !== '/login') window.location.assign('/login')
  }
  if (!res.ok || !res.body) throw new ApiError(`POST /chat/stream failed (${res.status})`, res.status)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: ChatResponse | null = null
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let split
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, split).trim()
      buffer = buffer.slice(split + 2)
      if (!chunk.startsWith('data:')) continue
      const evt = JSON.parse(chunk.slice(5))
      if (evt.type === 'token') onToken?.(evt.text as string)
      else if (evt.type === 'stage') onStage(evt as StageEvent)
      else if (evt.type === 'done') result = evt.payload as ChatResponse
      else if (evt.type === 'error')
        throw new ApiError(`chat failed: ${evt.message ?? 'unknown error'}`, 0)
    }
  }
  if (!result) throw new ApiError('chat stream ended without a result', 0)
  return result
}

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

const put = <T,>(path: string, body: unknown) =>
  request<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

const del = <T,>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  login: (email: string, password: string) =>
    post<LoginResponse>('/auth/login', { email, password }),
  register: (name: string, email: string, password: string) =>
    post<LoginResponse>('/auth/register', { name, email, password }),
  me: () => get<User>('/auth/me'),

  health: () => get<HealthResponse>('/health'),
  agents: () => get<AgentsResponse>('/agents'),
  chat: (req: ChatRequest) => post<ChatResponse>('/chat', req),
  chatStream,
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
  deleteConversation: (id: string) =>
    request<{ deleted: boolean }>(`/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  products: () => get<ProductsResponse>('/shop/products'),
  orders: () => get<{ orders: Order[] }>('/shop/orders'),
  cart: () => get<CartResponse>('/shop/cart'),
  addToCart: (productId: string) =>
    post<CartResponse>('/shop/cart/add', { product_id: productId }),
  decrementCart: (productId: string) =>
    post<CartResponse>('/shop/cart/decrement', { product_id: productId }),
  removeFromCart: (productId: string) =>
    post<CartResponse>('/shop/cart/remove', { product_id: productId }),
  checkout: (newCard = false) => post<CheckoutResponse>('/shop/checkout', { new_card: newCard }),

  // management (merchant: own shop; admin: all shops + users)
  manageProducts: () => get<{ shop: string | null; products: Product[] }>('/manage/products'),
  createProduct: (body: Record<string, unknown>) => post<Product>('/manage/products', body),
  updateProduct: (id: string, body: Record<string, unknown>) =>
    put<Product>(`/manage/products/${encodeURIComponent(id)}`, body),
  deleteProduct: (id: string) =>
    del<{ deleted: boolean }>(`/manage/products/${encodeURIComponent(id)}`),
  manageStats: () => get<ShopStats>('/manage/stats'),
  manageOrders: () => get<{ orders: ShopOrder[] }>('/manage/orders'),
  manageSales: () => get<ShopSales>('/manage/sales'),
  changes: (status = 'pending') =>
    get<{ changes: ProductChange[] }>(`/manage/changes?status=${encodeURIComponent(status)}`),
  approveChange: (id: string) =>
    post<{ status: string }>(`/manage/changes/${encodeURIComponent(id)}/approve`, {}),
  rejectChange: (id: string) =>
    post<{ status: string }>(`/manage/changes/${encodeURIComponent(id)}/reject`, {}),
  users: () => get<{ users: AccountRow[] }>('/manage/users'),
  updateUser: (email: string, body: { role: string; shop?: string | null }) =>
    put<{ updated: boolean }>(`/manage/users/${encodeURIComponent(email)}`, body),
  deleteUser: (email: string) =>
    del<{ deleted: boolean }>(`/manage/users/${encodeURIComponent(email)}`),
}

// --- tiny shared formatters (analytics numbers are nullable end to end) ---

export const fmtScore = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

export const fmtPct = (v: number | null | undefined) =>
  v == null ? '—' : `${(v * 100).toFixed(0)}%`

export const fmtMs = (v: number | null | undefined) =>
  v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : '—'
