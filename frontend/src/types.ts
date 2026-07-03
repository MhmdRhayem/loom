// TypeScript mirrors of the backend's pydantic response models
// (src/multi_agent_framework/api/models.py + analytics_models.py, demo shop_routes.py).
// Datetimes arrive as ISO-8601 strings.

// --- auth ---

export interface User {
  email: string
  name: string
}

export interface LoginResponse {
  token: string
  user: User
}

// --- chat ---

export interface ChatRequest {
  message: string
  conversation_id?: string | null
  owner_id?: string | null
}

export interface ToolCall {
  name?: string
  args?: Record<string, unknown>
  [key: string]: unknown
}

export interface AgentRun {
  agent: string
  text: string | null
  tool_calls: ToolCall[]
}

export interface AgentEval {
  agent?: string
  score?: number
  pass?: boolean
  feedback?: string
  [key: string]: unknown
}

export interface EvalResult {
  stage?: string
  score?: number
  pass?: boolean
  feedback?: string
  agent_evals?: AgentEval[]
  [key: string]: unknown
}

export interface ChatResponse {
  conversation_id: string
  agent: string | null
  response: string | null
  eval: EvalResult | null
  current_agents: string[]
  routing_confidence: number | null
  routing_reason: string | null
  query_category: string | null
  tool_calls: ToolCall[]
  agent_runs: AgentRun[]
  retry_count: number
}

export interface FeedbackRequest {
  conversation_id: string
  rating: 'up' | 'down'
  comment?: string | null
}

export interface FeedbackResponse {
  recorded: boolean
  agent: string | null
}

// --- health / roster ---

export interface HealthResponse {
  status: string
  components: Record<string, string>
}

export interface AgentInfo {
  name: string
  description: string
  capabilities: string[]
  tools: string[]
  model: string // tier: fast | standard | deep
  max_tokens: number
  eval_rubric: string[]
  judge_sample_rate: number
  fallback_agent: string
  memory_scope: string
}

export interface AgentsResponse {
  agents: AgentInfo[]
  fallback_agent: string | null
}

// --- analytics ---

export interface OverviewResponse {
  conversations: number
  turns: number
  avg_eval_score: number | null
  avg_latency_ms: number | null
  eval_pass_rate: number | null
  retry_rate: number | null
  total_memories: number
  dream_runs: number
}

export interface AgentTurnStats {
  agent: string
  turns: number
  avg_eval_score: number | null
  passes: number
  fails: number
  avg_latency_ms: number | null
}

export interface AgentPerfRow {
  agent: string
  category: string
  score: number | null
  successes: number
  failures: number
}

export interface AgentAnalyticsResponse {
  agents: AgentTurnStats[]
  performance: AgentPerfRow[]
}

export interface DistributionSlice {
  key: string
  count: number
}

export interface RoutingAnalyticsResponse {
  category_distribution: DistributionSlice[]
  agent_distribution: DistributionSlice[]
  avg_routing_confidence: number | null
}

export interface TimePoint {
  bucket: string | null
  turns: number
  avg_eval_score: number | null
  avg_latency_ms: number | null
}

export interface TimeseriesResponse {
  points: TimePoint[]
}

export interface MemoryCount {
  owner_id: string
  count: number
}

export interface DreamRunInfo {
  owner_id: string
  sessions_consolidated: number
  memories_merged: number
  memories_pruned: number
  duration_ms: number
  started_at: string
}

export interface MemoryAnalyticsResponse {
  memory_counts: MemoryCount[]
  dream_runs: DreamRunInfo[]
}

// --- conversations ---

export interface ConversationSummary {
  id: string
  owner_id: string
  created_at: string
  status: string
  total_turns: number
  title: string | null // first user message, truncated — the list's display name
}

export interface ConversationListResponse {
  conversations: ConversationSummary[]
}

export interface TurnDetail {
  turn_number: number
  user_message: string
  agent_name: string | null
  query_category: string | null
  routing_confidence: number | null
  agent_response: string | null
  eval_score: number | null
  retry_count: number
  model_tier: string | null
  latency_ms: number | null
  created_at: string
}

export interface TurnAgentInfo {
  turn_number: number
  agent_name: string
  model_tier: string | null
  eval_score: number | null
  eval_pass: boolean | null
}

export interface ConversationDetailResponse {
  conversation: ConversationSummary
  turns: TurnDetail[]
  turn_agents: TurnAgentInfo[]
}

// --- demo storefront ---

export interface Product {
  id: string
  name: string
  price: number
  rating: number
  in_stock: boolean
  category: string
  description: string
  deal: string | null
}

export interface Order {
  order_id: string
  email: string
  status: string
  carrier: string | null
  tracking_number: string | null
  estimated_delivery: string | null
  placed_on: string | null
  total: number
}

export interface Coupon {
  code: string
  discount_pct: number
  product_id: string | null
  min_subtotal: number | null
}
