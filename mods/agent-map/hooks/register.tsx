import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { LiveAgent, Superlite, Usage } from '../types'
import {
  elapsedMs,
  emptySuperlite,
  fileActive,
  fmtDuration,
  fmtTokens,
  ledgerFor,
  mergeCards,
  parseLedger,
  parseMetrics,
  parsePool,
  subagentTranscript,
  tierOf,
  totalTokens,
  transcriptUsage,
} from './model'
import type { Card, Status } from './model'
import { graphSvg, treeOrder } from './graph'
import type { GraphNode } from './graph'

const PANE = 'superlite-agents'
const TITLE = 'Agent map'
const TOOL = 'open_agent_map'

const live = atom({ plugin: 'superlite-agents', key: 'live' } as const, {})
const usage = atom({ plugin: 'superlite-agents', key: 'usage' } as const, {})
const files = atom({ plugin: 'superlite-agents', key: 'files' } as const, emptySuperlite())
const selected = atom({ plugin: 'superlite-agents', key: 'selected' } as const, null)
const now = atom({ plugin: 'superlite-agents', key: 'now' } as const, 0)
const mainActive = atom({ plugin: 'superlite-agents', key: 'mainActive' } as const, false)
const mainModel = atom({ plugin: 'superlite-agents', key: 'mainModel' } as const, '')

type $ = EngineInterface

// Each transcript's mtime when last read: read again only when it moved.
const readAt = new Map<string, number>()

async function upsert($: $, id: string, fn: (prev: LiveAgent | undefined) => LiveAgent): Promise<void> {
  await update($, live, (all: Record<string, LiveAgent> | undefined) => ({ ...(all ?? {}), [id]: fn(all?.[id]) }))
}

async function markStarted($: $, id: string, fields: Partial<LiveAgent>): Promise<void> {
  const t = await $.clock.now()
  await upsert($, id, prev => ({
    id,
    type: fields.type || prev?.type || '',
    description: fields.description || prev?.description || '',
    model: fields.model ?? prev?.model,
    startedAt: prev?.startedAt ?? t,
    stoppedAt: undefined,
    isActive: true,
    isAborted: false,
    parent: fields.parent ?? prev?.parent,
    transcript: fields.transcript ?? prev?.transcript,
    last: prev?.last,
  }))
  await update($, now, () => t)
}

async function markStopped($: $, id: string, fields: Partial<LiveAgent>): Promise<void> {
  const t = await $.clock.now()
  await upsert($, id, prev => ({
    id,
    type: fields.type || prev?.type || '',
    description: prev?.description || '',
    model: prev?.model,
    startedAt: prev?.startedAt,
    stoppedAt: t,
    isActive: false,
    isAborted: fields.isAborted ?? prev?.isAborted,
    parent: prev?.parent,
    transcript: fields.transcript ?? prev?.transcript,
    last: fields.last ?? prev?.last,
  }))
  await update($, now, () => t)
}

async function readText($: $, path: string): Promise<string | undefined> {
  try {
    return await $.fs.read(path)
  } catch {
    return undefined
  }
}

/** Re-reads the .superlite records and the transcripts not yet final; every read may fail, none throws. */
async function refresh($: $): Promise<void> {
  const dir = (await read($, files)).dir
  const fresh: Superlite = emptySuperlite(dir)
  const metrics = await readText($, `${dir}/metrics.jsonl`)
  if (metrics !== undefined) Object.assign(fresh, { hasMetrics: true }, parseMetrics(metrics))
  const pool = await readText($, `${dir}/pool.json`)
  if (pool !== undefined) fresh.pool = parsePool(pool)
  const ledger = await readText($, `${dir}/progress.md`)
  if (ledger !== undefined) Object.assign(fresh, parseLedger(ledger))

  try {
    for (const a of await $.agent.list()) {
      const running = a.status === 'running' || a.status === 'pending' || a.status === 'waiting'
      const known = (await read($, live))[a.id]
      const failed = a.status === 'failed' || a.status === 'killed'
      if (known && known.isActive === running && (running || !!known.isAborted === failed)) continue
      if (running) await markStarted($, a.id, { type: a.type, description: a.description, parent: a.parentId })
      else if (known) await markStopped($, a.id, { isAborted: failed })
    }
  } catch {
    // no agent list on this host: the events alone
  }

  const agents = await read($, live)
  const got: Record<string, Usage> = {}
  const mtimes: Record<string, number> = {}
  for (const id of new Set([...Object.keys(agents), ...Object.keys(fresh.stops)])) {
    const path = agents[id]?.transcript ?? fresh.stops[id]?.transcript
    if (!path) continue
    let mtime: number
    try {
      mtime = (await $.fs.stat(path)).mtimeMs
    } catch {
      continue
    }
    mtimes[id] = mtime
    if (readAt.get(id) === mtime) continue
    const text = await readText($, path)
    if (text === undefined) continue
    got[id] = transcriptUsage(text)
    readAt.set(id, mtime)
  }
  fresh.active = fileActive(fresh, mtimes, await $.clock.now())
  await update($, files, () => fresh)
  if (Object.keys(got).length) await update($, usage, (all: Record<string, Usage> | undefined) => ({ ...(all ?? {}), ...got }))
}

async function openPane($: $): Promise<void> {
  const r = await $.ui.open({ id: PANE, title: TITLE })
  if (!r.isPlaced) await $.ui.toast(`Agent map is open but no surface shows it now: ${JSON.stringify(r)}`)
}

function dirOf(args: string): string | undefined {
  const a = args.trim().replace(/^["']|["']$/g, '').replace(/[\\/]+$/, '')
  if (!a) return undefined
  return /(^|[\\/])\.superlite$/.test(a) ? a : `${a}/.superlite`
}

/** Points the map at a project (or its .superlite folder) when given one, refreshes it and opens the pane. */
async function show($: $, args: string): Promise<string> {
  const dir = dirOf(args)
  if (dir) {
    readAt.clear()
    await update($, usage, () => ({}))
    await update($, files, () => emptySuperlite(dir))
    await $.store.set('dir', dir).catch(() => undefined)
  }
  await update($, selected, () => null)
  const t = await $.clock.now()
  await update($, now, () => t)
  await refresh($).catch(() => undefined)
  await openPane($)
  const f = await read($, files)

  return f.hasMetrics ? `Agent map opened (${f.dir}).` : 'Agent map opened (no .superlite records here; live events only).'
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'superlite-agents',
      description: 'Show the agent map: the main session and each subagent, read-only',
      argumentHint: '[project dir]',
      immediate: true,
    })
    await $.tool.register({
      name: TOOL,
      description:
        'Opens the read-only agent map pane beside the transcript: the main session and each subagent with its tier, time, tokens, tasks and ledger lines. Call it when the user asks to watch a superlite run. dir: the project folder (or its .superlite folder); omit for the working directory.',
      inputSchema: { type: 'object', properties: { dir: { type: 'string' } } },
      isDeferred: false,
    }).catch(() => undefined)
    try {
      const model = await $.session.model()
      await update($, mainModel, () => model)
    } catch {
      // no model yet
    }
    const kept = await $.store.get('dir').catch(() => undefined)
    if (typeof kept === 'string' && kept) await update($, files, (f: Superlite | undefined) => ({ ...(f ?? emptySuperlite()), dir: kept }))
    void refresh($).catch(() => undefined)

    let ticks = 0
    $.clock.every(1000, () => {
      ticks += 1
      void (async () => {
        const agents = await read($, live)
        const busy =
          (await read($, mainActive)) || Object.values(agents).some(a => a.isActive) || (await read($, files)).active.length > 0
        if (busy) {
          const t = await $.clock.now()
          await update($, now, () => t)
        }
        if (ticks % 3 === 0) await refresh($)
      })().catch(() => undefined)
    })

    return next(e)
  })

  on('command.run', { command: 'superlite-agents' }, async ($, e) => ({ text: await show($, e.args) }))

  on('tool.call', { tool: 'mcp__superlite-agents__open_agent_map' }, async ($, e) => {
    const dir = (e as { dir?: unknown }).dir
    const text = await show($, typeof dir === 'string' ? dir : '')

    return { result: text, text, isReadOnly: true as const }
  })

  on('agent.spawn', async ($, e, next) => {
    const ran = await next(e)
    try {
      if (ran.agentId)
        await markStarted($, ran.agentId, { type: e.subagentType, description: e.description, model: ran.model, parent: e.parentAgentId })
    } catch {
      // the map is a view: never in the spawn's way
    }

    return ran
  }).catch(($, e, next) => next(e))

  on('classic.SubagentStart', async ($, e, next) => {
    await markStarted($, e.agent_id, { type: e.agent_type, transcript: subagentTranscript(e.transcript_path, e.agent_id) }).catch(
      () => undefined,
    )

    return next(e)
  })

  on('classic.SubagentStop', async ($, e, next) => {
    await markStopped($, e.agent_id, {
      type: e.agent_type,
      transcript: e.agent_transcript_path || undefined,
      last: e.last_assistant_message,
    }).catch(() => undefined)
    readAt.delete(e.agent_id)
    void refresh($).catch(() => undefined)

    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    if (!e.agentId) await update($, mainActive, () => true).catch(() => undefined)

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (!e.agentId) await update($, mainActive, () => false).catch(() => undefined)
    else if (e.isAborted || e.reason === 'aborted' || e.reason === 'error')
      await markStopped($, e.agentId, { isAborted: true }).catch(() => undefined)

    return done
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const cards = mergeCards(await read($, live), await read($, files), await read($, usage))
    const at = (await read($, now)) || Date.now()
    const pick = await read($, selected)
    const f = await read($, files)
    const isMainActive = await read($, mainActive)
    const model = await read($, mainModel)
    const cols = e.props.bodyColumns
    const active = cards.filter(c => c.isActive).length
    const tone = { running: 'success', aborted: 'error', done: 'inactive' } as const
    const dot = (st: Status) => <Text color={tone[st]}>●</Text>
    const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s)
    const shortType = (t: string) => t.split(':').pop() || '-'
    const cardWidth = Math.max(24, Math.min(36, Math.floor(((cols || 80) - 28) / 2) - 1))

    const head = (
      <Text>
        <Text weight="semibold">{TITLE}</Text>
        <Text dimColor>
          {' '}
          {cards.length} agents · {active} active{f.hasMetrics ? ` · ${f.dir}` : ''}
        </Text>
      </Text>
    )

    const card = pick ? cards.find(c => c.id === pick) : undefined
    if (card) {
      const t = card.usage?.tokens
      const rows: [string, string][] = [
        ['Agent', card.id],
        ['Type', card.type || '-'],
        ['Model', card.model ?? '-'],
        ['Tier', card.tier ?? '-'],
        ['Tasks', card.tasks.join(' ') || '-'],
        ['Status', `${card.status}${card.isKnownLive ? '' : ' (from the run files)'}`],
        ['Parent', card.parent ? (cards.find(c => c.id === card.parent)?.description ?? card.parent) : 'main session'],
        ['Started', card.startedAt ? new Date(card.startedAt).toISOString() : '-'],
        ['Elapsed', fmtDuration(elapsedMs(card, at))],
        ['Tokens', t ? `${totalTokens(t)} (in ${t.input}, out ${t.output}, cache read ${t.cacheRead}, cache write ${t.cacheWrite})` : '-'],
        ['Tool calls', card.usage ? String(card.usage.toolCalls) : '-'],
        ['Transcript', card.transcript ?? '-'],
      ]

      return (
        <Box flexDirection="column">
          {head}
          <Button key="back" plain onPress={() => update($, selected, () => null)}>
            ‹ Back to map
          </Button>
          <Text>
            {dot(card.status)} <Text weight="semibold">{card.description || card.type || card.id}</Text>
          </Text>
          {rows.map(([k, v]) => (
            <Text>
              <Text dimColor>{k.padEnd(11)}</Text>
              {v}
            </Text>
          ))}
          {ledgerFor(f.ledger, card).length ? <Text dimColor>Ledger</Text> : null}
          {ledgerFor(f.ledger, card)
            .slice(-12)
            .map(l => (
              <Text>{l}</Text>
            ))}
          {card.last ? <Text dimColor>Last message</Text> : null}
          {card.last ? <Text>{card.last.slice(0, 1200)}</Text> : null}
        </Box>
      )
    }

    const metaOf = (c: Card) => `${c.tier ?? '?'} · ${fmtDuration(elapsedMs(c, at))} · ${fmtTokens(c.usage ? totalTokens(c.usage.tokens) : undefined)}`
    const tasksOf = (c: Card) =>
      c.tasks.length ? c.tasks.map(t => (c.doneTasks.includes(t) ? `${t}✓` : t)).join(' ') : shortType(c.type)
    const mainMeta = `${tierOf(model) ?? (model || '-')} · ${cards.length} subagents · ${active} active`
    const ledgerRows = [
      f.ledger.length > 0 ? <Text dimColor>Ledger (progress.md)</Text> : null,
      ...f.ledger.slice(-8).map(l => <Text>{l}</Text>),
    ]

    const mainStatus: Status = isMainActive || active > 0 ? 'running' : 'done'
    const nodes: GraphNode[] = cards.map(c => ({
      id: c.id,
      parent: c.parent,
      title: c.description || c.type || c.id,
      meta: metaOf(c),
      tasks: tasksOf(c),
      status: c.status,
      type: c.type,
    }))
    const main = { title: 'Main session', meta: mainMeta, status: mainStatus }

    const byId = new Map(cards.map(c => [c.id, c]))
    const tree = [
      <Text>
        {dot(mainStatus)} <Text weight="semibold">Main session</Text> <Text dimColor>{mainMeta}</Text>
      </Text>,
      ...(cards.length === 0 ? [<Text dimColor>└─ no subagents yet</Text>] : []),
      ...treeOrder(main, nodes).map(row => {
        const c = byId.get(row.id)!
        const lead = row.trail.map(more => (more ? '│  ' : '   ')).join('') + (row.isLast ? '└─ ' : '├─ ')
        return (
          <Box key={`box-${c.id}`} flexDirection="row">
            <Text dimColor>{lead}</Text>
            <Button key={`card-${c.id}`} plain onPress={() => update($, selected, () => c.id)}>
              {dot(c.status)} {clip(c.description || c.type || c.id, Math.max(16, cardWidth))}{' '}
              <Text dimColor>
                {metaOf(c)} · {tasksOf(c)}
              </Text>
            </Button>
          </Box>
        )
      }),
    ]

    if (e.surface !== 'terminal') {
      const { Svg } = $.ui.resolve(e)
      const g = graphSvg(main, nodes, Math.max(420, (cols || 110) * 8))
      return (
        <Box flexDirection="column" gap={1}>
          {head}
          <Svg source={g.source} alt={`Agent map: main session and ${cards.length} subagents, ${active} running`} width={g.width} height={g.height} isInteractive />
          <Box flexDirection="column">{tree}</Box>
          <Box flexDirection="column">{ledgerRows}</Box>
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {head}
        {tree}
        {ledgerRows}
      </Box>
    )
  })
}
