export type Tokens = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
}

/** One subagent as this session's own start and stop events saw it. */
export type LiveAgent = {
  id: string
  type: string
  description: string
  model?: string
  startedAt?: number
  stoppedAt?: number
  isActive: boolean
  /** The engine said its loop failed, was killed or its turn aborted. */
  isAborted?: boolean
  /** The agent whose loop spawned it; absent when the main session did. */
  parent?: string
  transcript?: string
  last?: string
}

/** What an agent's transcript adds: tokens deduplicated by message id, tool calls, first and last message times. */
export type Usage = {
  tokens: Tokens
  toolCalls: number
  firstMs?: number
  lastMs?: number
  model?: string
}

export type MetricsLaunch = { id: string; t?: number; type?: string; model?: string; desc?: string; parent?: string }
export type MetricsStop = { id: string; t?: number; first?: number; type?: string; transcript?: string; last?: string }
export type PoolEntry = { tasks: string[]; tier?: string; current?: string }

/** The project's `.superlite` records, parsed; empty where the files are missing. */
export type Superlite = {
  dir: string
  hasMetrics: boolean
  launches: Record<string, MetricsLaunch>
  stops: Record<string, MetricsStop>
  pool: Record<string, PoolEntry>
  /** Agents the files show at work (for a run in another process, whose events never reach this session). */
  active: string[]
  /** progress.md: the coordinator's ledger lines, oldest first. */
  ledger: string[]
  /** Tasks the ledger sent to each agent id (`sent T3+T1 to <id>`). */
  sent: Record<string, string[]>
  /** Tasks the ledger checkpointed (`checkpoint T0 <sha> ...`). */
  done: string[]
}

declare module 'claude-code' {
  interface PluginState {
    'superlite-agents': {
      live: Record<string, LiveAgent>
      usage: Record<string, Usage>
      files: Superlite
      selected: string | null
      now: number
      mainActive: boolean
      mainModel: string
    }
  }
}
