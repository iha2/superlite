// Pure parsing and merging for the agent map: no `$`, so tests call it directly.
// Token counting follows skills/subagent-driven-development/scripts/metrics.py
// (transcript_segments): usage per assistant message, deduplicated by message id
// keeping the largest value per field, output topped up from the text length.
import type { LiveAgent, MetricsLaunch, MetricsStop, PoolEntry, Superlite, Tokens, Usage } from '../types'

const FAMILIES = ['haiku', 'sonnet', 'opus'] as const
const TID = /\bT(\d+[a-z]?)\b/
const CHARS_PER_TOKEN = 3.5
// a stop whose last words say the agent did not finish its work
const ABORTED = /\bStatus:\s*\**\s*BLOCKED\b|\[Request interrupted|\binterrupted by user\b|\baborted\b/i

export type Tier = (typeof FAMILIES)[number]

/** running: green; aborted: red; done: grey. */
export type Status = 'running' | 'aborted' | 'done'

export type Card = {
  id: string
  type: string
  description: string
  model?: string
  tier?: Tier
  tasks: string[]
  doneTasks: string[]
  startedAt?: number
  stoppedAt?: number
  isActive: boolean
  status: Status
  /** The agent that spawned it; absent when the main session did. */
  parent?: string
  isKnownLive: boolean
  transcript?: string
  last?: string
  usage?: Usage
}

type Obj = Record<string, unknown>

const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined)
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

export function parseJsonl(text: string): Obj[] {
  const out: Obj[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue
    try {
      const v = JSON.parse(line)
      if (v && typeof v === 'object' && !Array.isArray(v)) out.push(v as Obj)
    } catch {
      // a torn last line while the hooks append: skip it
    }
  }
  return out
}

export function emptySuperlite(dir = '.superlite'): Superlite {
  return { dir, hasMetrics: false, launches: {}, stops: {}, pool: {}, active: [], ledger: [], sent: {}, done: [] }
}

/** Launch and stop rows by agent id: the first launch, the last stop (as metrics.py joins them). */
export function parseMetrics(text: string): Pick<Superlite, 'launches' | 'stops'> {
  const launches: Record<string, MetricsLaunch> = {}
  const stops: Record<string, MetricsStop> = {}
  for (const e of parseJsonl(text)) {
    const id = str(e.agent_id)
    if (!id) continue
    if (e.ev === 'launch' && !launches[id]) {
      launches[id] = { id, t: num(e.t), type: str(e.type), model: str(e.model), desc: str(e.desc), parent: str(e.parent) }
    } else if (e.ev === 'stop') {
      const prev = stops[id]
      stops[id] = {
        id,
        t: num(e.t) ?? prev?.t,
        first: prev?.first ?? num(e.t),
        type: str(e.type) ?? prev?.type,
        transcript: str(e.transcript) ?? prev?.transcript,
        last: str(e.last) ?? prev?.last,
      }
    }
  }
  return { launches, stops }
}

export function parsePool(text: string): Record<string, PoolEntry> {
  const out: Record<string, PoolEntry> = {}
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return out
  }
  const agents = doc && typeof doc === 'object' ? (doc as Obj).agents : undefined
  if (!Array.isArray(agents)) return out
  for (const a of agents) {
    if (!a || typeof a !== 'object') continue
    const id = (a as Obj).id
    if (id === undefined || id === null) continue
    const tasks = (a as Obj).tasks
    out[String(id)] = {
      tasks: Array.isArray(tasks) ? tasks.filter((t): t is string => typeof t === 'string') : [],
      tier: str((a as Obj).tier),
      current: str((a as Obj).current),
    }
  }
  return out
}

const SENT = /\bsent\s+(?:R:)?(T\d+[a-z]?(?:\s*[+,]\s*T\d+[a-z]?)*)\s+to\s+(\S+)/i
const CHECKPOINT = /\bcheckpoint\s+(T\d+[a-z]?)\b/i

/** progress.md: its lines, the tasks sent to each agent, the tasks checkpointed. */
export function parseLedger(text: string): Pick<Superlite, 'ledger' | 'sent' | 'done'> {
  const ledger: string[] = []
  const sent: Record<string, string[]> = {}
  const done: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim().replace(/^[-*]\s+/, '')
    if (!line || line.startsWith('#')) continue
    ledger.push(line)
    const s = SENT.exec(line)
    if (s) {
      const list = (sent[s[2]!] ??= [])
      for (const t of s[1]!.split(/\s*[+,]\s*/)) if (!list.includes(t)) list.push(t)
    }
    const c = CHECKPOINT.exec(line)
    if (c && !done.includes(c[1]!)) done.push(c[1]!)
  }
  return { ledger, sent, done }
}

/** The ledger lines about one agent: its id, or one of its tasks as a word. */
export function ledgerFor(ledger: string[], card: Pick<Card, 'id' | 'tasks'>): string[] {
  const words = card.tasks.map(t => new RegExp(`\\b${t}\\b`))
  return ledger.filter(l => l.includes(card.id) || words.some(w => w.test(l)))
}

function msgChars(content: unknown): number {
  if (!Array.isArray(content)) return 0
  let n = 0
  for (const b of content) {
    if (!b || typeof b !== 'object') continue
    const o = b as Obj
    n += (str(o.text)?.length ?? 0) + (str(o.thinking)?.length ?? 0)
    if (o.type === 'tool_use') n += JSON.stringify(o.input ?? {}).length
  }
  return n
}

/** One agent transcript (JSONL) to its usage. */
export function transcriptUsage(text: string): Usage {
  const tokens: Tokens = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }
  const seen = new Map<string, { rec: Tokens; chars: number }>()
  const calls = new Set<string>()
  let firstMs: number | undefined
  let lastMs: number | undefined
  let model: string | undefined
  for (const e of parseJsonl(text)) {
    if (e.type !== 'assistant') continue
    const msg = (e.message && typeof e.message === 'object' ? e.message : {}) as Obj
    const ts = str(e.timestamp)
    const t = ts ? Date.parse(ts) : NaN
    if (Number.isFinite(t)) {
      firstMs = firstMs ?? t
      lastMs = t
    }
    model = str(msg.model) ?? model
    const mid = str(msg.id) ?? `anon${seen.size}`
    const u = (msg.usage && typeof msg.usage === 'object' ? msg.usage : {}) as Obj
    const rec: Tokens = {
      input: num(u.input_tokens) ?? 0,
      output: num(u.output_tokens) ?? 0,
      cacheRead: num(u.cache_read_input_tokens) ?? 0,
      cacheWrite: num(u.cache_creation_input_tokens) ?? 0,
    }
    const prev = seen.get(mid)
    if (prev) {
      prev.chars += msgChars(msg.content)
      for (const k of Object.keys(rec) as (keyof Tokens)[]) {
        if (rec[k] > prev.rec[k]) {
          tokens[k] += rec[k] - prev.rec[k]
          prev.rec[k] = rec[k]
        }
      }
    } else {
      seen.set(mid, { rec: { ...rec }, chars: msgChars(msg.content) })
      for (const k of Object.keys(rec) as (keyof Tokens)[]) tokens[k] += rec[k]
    }
    if (Array.isArray(msg.content)) {
      for (const b of msg.content) {
        if (b && typeof b === 'object' && (b as Obj).type === 'tool_use') calls.add(str((b as Obj).id) ?? `${mid}:${calls.size}`)
      }
    }
  }
  for (const v of seen.values()) {
    const est = Math.floor(v.chars / CHARS_PER_TOKEN)
    if (est > v.rec.output) tokens.output += est - v.rec.output
  }
  return { tokens, toolCalls: calls.size, firstMs, lastMs, model }
}

export function totalTokens(t: Tokens): number {
  return t.input + t.output + t.cacheRead + t.cacheWrite
}

export function tierOf(...hints: (string | undefined)[]): Tier | undefined {
  for (const h of hints) {
    const m = (h ?? '').toLowerCase()
    const f = FAMILIES.find(x => m.includes(x))
    if (f) return f
  }
  return undefined
}

/** Where a subagent's transcript lives, from the main session's: `<session>/subagents/agent-<id>.jsonl`. */
export function subagentTranscript(mainTranscript: string | undefined, id: string): string | undefined {
  if (!mainTranscript || !mainTranscript.endsWith('.jsonl')) return undefined
  return `${mainTranscript.slice(0, -'.jsonl'.length)}/subagents/agent-${id}.jsonl`
}

/**
 * Every agent this session started, and every agent the superlite records name,
 * as one card each, oldest first. Live status is the engine's events where this
 * session saw the agent, else what the files show (`fileActive`).
 */
export function mergeCards(live: Record<string, LiveAgent>, files: Superlite, usage: Record<string, Usage>): Card[] {
  const ids = new Set([...Object.keys(live), ...Object.keys(files.launches), ...Object.keys(files.stops)])
  const cards: Card[] = []
  for (const id of ids) {
    const l = live[id]
    const la = files.launches[id]
    const st = files.stops[id]
    const pool = files.pool[id]
    const u = usage[id]
    const description = l?.description || la?.desc || ''
    const tid = TID.exec(description)
    const fromLedger = files.sent[id] ?? []
    const known = [...(pool?.tasks ?? []), ...fromLedger.filter(t => !pool?.tasks.includes(t))]
    const tasks = known.length ? known : tid ? [`T${tid[1]}`] : []
    const model = l?.model ?? la?.model ?? u?.model
    const type = l?.type || la?.type || st?.type || ''
    const isActive = l ? l.isActive : files.active.includes(id)
    const last = l?.last ?? st?.last
    const status: Status = isActive
      ? 'running'
      : l?.isAborted || ABORTED.test(last ?? '') || (!l && !st && la?.t !== undefined)
        ? 'aborted'
        : 'done'
    cards.push({
      id,
      type,
      description,
      model,
      tier: tierOf(pool?.tier, model, type),
      tasks,
      doneTasks: tasks.filter(t => files.done.includes(t)),
      startedAt: l?.startedAt ?? earliest(la?.t, st?.first, u?.firstMs),
      stoppedAt: l ? l.stoppedAt : (st?.t ?? u?.lastMs),
      isActive,
      status,
      parent: l?.parent ?? la?.parent,
      isKnownLive: l !== undefined,
      transcript: l?.transcript ?? st?.transcript,
      last,
      usage: u,
    })
  }
  return cards.sort((a, b) => (a.startedAt ?? Infinity) - (b.startedAt ?? Infinity) || a.id.localeCompare(b.id))
}

const FRESH_MS = 20_000
const LAUNCH_MS = 15 * 60_000

/**
 * Agents at work by the files alone: launched and not stopped since (within 15
 * minutes of the launch), or whose transcript changed in the last 20 seconds.
 */
export function fileActive(files: Pick<Superlite, 'launches' | 'stops'>, mtimes: Record<string, number>, now: number): string[] {
  const ids = new Set([...Object.keys(files.launches), ...Object.keys(files.stops), ...Object.keys(mtimes)])
  return [...ids].filter(id => {
    const la = files.launches[id]
    const st = files.stops[id]
    const m = mtimes[id]
    if (m !== undefined && now - m < FRESH_MS) return true
    return la?.t !== undefined && !st && now - la.t < LAUNCH_MS
  })
}

// superlite writes an agent's launch row when its Agent call returns, after the prime turn's stop.
function earliest(...ts: (number | undefined)[]): number | undefined {
  const known = ts.filter((t): t is number => t !== undefined)
  return known.length ? Math.min(...known) : undefined
}

/** Elapsed time: to now while active, to the stop otherwise; undefined when the start is unknown. */
export function elapsedMs(card: Card, now: number): number | undefined {
  if (card.startedAt === undefined) return undefined
  const end = card.isActive ? now : (card.stoppedAt ?? card.usage?.lastMs)
  return end === undefined ? undefined : Math.max(0, end - card.startedAt)
}

export function fmtDuration(ms: number | undefined): string {
  if (ms === undefined) return '-'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

export function fmtTokens(n: number | undefined): string {
  if (n === undefined) return '- tok'
  if (n < 1000) return `${n} tok`
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k tok`
  return `${(n / 1_000_000).toFixed(2)}M tok`
}
