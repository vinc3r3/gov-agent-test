export type QuestionType = 'search' | 'comparison' | 'analytics' | 'anomaly' | 'fairness' | 'volume'

export interface Confidence {
  level: string
  reasons: string[]
  limitations: string[]
}

export interface Sections {
  verdict: string
  data?: {
    period: unknown
    bins: unknown
    filters: Record<string, unknown>
    target?: Record<string, unknown> | null
    n_objects: Record<string, unknown>
    sources: string[]
  }
  analytics?: Record<string, unknown>
  metric?: string
  confidence?: Confidence
  top_k?: { columns: string[]; items: Record<string, unknown>[] }
}

export interface TraceStep {
  tool: string
  input: Record<string, unknown>
  error: string | null
  summary?: Record<string, unknown>
  sample?: Record<string, unknown>
}

export interface Answer {
  id: string
  question: string
  question_type: QuestionType
  language: 'ru' | 'kk'
  sections: Sections
  markdown: string
  trace: TraceStep[]
  latency_s: number
  llm: { provider: string; model: string; usage: { input: number; output: number } }
  evidence: unknown[]
}

export interface Health {
  status: string
  llm: { provider: string; model: string }
  last_successful_sync: string | null
  counts: Record<string, number>
}
