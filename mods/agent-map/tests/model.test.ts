import { describe, expect, test } from 'claude-code/testing'

import {
  elapsedMs,
  emptySuperlite,
  fileActive,
  ledgerFor,
  parseLedger,
  fmtTokens,
  mergeCards,
  parseMetrics,
  parsePool,
  subagentTranscript,
  tierOf,
  totalTokens,
  transcriptUsage,
} from '../hooks/model'
import { COLORS, graphSvg, roleOf, ROLES, treeOrder } from '../hooks/graph'

const METRICS = [
  '{"ev":"launch","t":1000,"agent_id":"a1","type":"superlite:implementer","model":"claude-sonnet-5-5","desc":"T3 templates"}',
  '{"ev":"launch","t":1500,"agent_id":"a2","type":"superlite:implementer-haiku","model":"claude-haiku-5-5","desc":"T0 skeleton"}',
  '{"ev":"stop","t":61000,"agent_id":"a1","type":"superlite:implementer","transcript":"C:/t/agent-a1.jsonl","last":"DONE"}',
  '{"ev":"launch","t":9999,"agent_id":"a1","desc":"a second launch is ignored"}',
  '{"ev":"stop","t":2',
].join('\n')

const TRANSCRIPT = [
  '{"type":"user","message":{"content":"T3 build templates"}}',
  '{"type":"assistant","timestamp":"2026-10-20T10:00:00Z","message":{"id":"m1","model":"claude-sonnet-5-5","usage":{"input_tokens":10,"output_tokens":1,"cache_read_input_tokens":1000,"cache_creation_input_tokens":200},"content":[{"type":"text","text":"ok"}]}}',
  '{"type":"assistant","timestamp":"2026-10-20T10:00:05Z","message":{"id":"m1","model":"claude-sonnet-5-5","usage":{"input_tokens":10,"output_tokens":40,"cache_read_input_tokens":1000,"cache_creation_input_tokens":200},"content":[{"type":"tool_use","id":"tu1","input":{}}]}}',
  '{"type":"assistant","timestamp":"2026-10-20T10:01:00Z","message":{"id":"m2","usage":{"input_tokens":5,"output_tokens":20,"cache_read_input_tokens":1200,"cache_creation_input_tokens":0},"content":[{"type":"text","text":"Status: DONE"}]}}',
].join('\n')

describe('metrics.jsonl', () => {
  test('joins the first launch and the last stop by agent id, skipping a torn line', () => {
    const { launches, stops } = parseMetrics(METRICS)
    expect(launches.a1?.desc).toBe('T3 templates')
    expect(launches.a1?.t).toBe(1000)
    expect(stops.a1?.transcript).toBe('C:/t/agent-a1.jsonl')
    expect(stops.a2).toBeUndefined()
  })

  test('pool.json gives tasks and tier; garbage gives nothing', () => {
    const pool = parsePool('{"agents":[{"id":"a1","tasks":["T3","T4",5],"tier":"sonnet"}]}')
    expect(pool.a1?.tasks).toEqual(['T3', 'T4'])
    expect(pool.a1?.tier).toBe('sonnet')
    expect(parsePool('not json')).toEqual({})
    expect(parsePool('{"agents":7}')).toEqual({})
  })
})

describe('transcripts', () => {
  test('usage is deduplicated by message id, keeping the largest value per field', () => {
    const u = transcriptUsage(TRANSCRIPT)
    expect(u.tokens.input).toBe(15)
    expect(u.tokens.output).toBe(60)
    expect(u.tokens.cacheRead).toBe(2200)
    expect(u.tokens.cacheWrite).toBe(200)
    expect(totalTokens(u.tokens)).toBe(2475)
    expect(u.toolCalls).toBe(1)
    expect(u.lastMs! - u.firstMs!).toBe(60000)
    expect(u.model).toBe('claude-sonnet-5-5')
  })

  test('an empty or broken transcript is zero, not a crash', () => {
    expect(totalTokens(transcriptUsage('').tokens)).toBe(0)
    expect(transcriptUsage('{{{\n[1]').toolCalls).toBe(0)
  })

  test('a subagent transcript sits beside the main one', () => {
    expect(subagentTranscript('C:/p/abc.jsonl', 'a1')).toBe('C:/p/abc/subagents/agent-a1.jsonl')
    expect(subagentTranscript('', 'a1')).toBeUndefined()
  })
})

describe('cards', () => {
  test('metrics rows become cards with tier, tasks, time and tokens', () => {
    const files = { ...emptySuperlite(), hasMetrics: true, ...parseMetrics(METRICS), pool: parsePool('{"agents":[{"id":"a1","tasks":["T3","T4"]}]}') }
    const cards = mergeCards({}, files, { a1: transcriptUsage(TRANSCRIPT) })
    expect(cards.map(c => c.id)).toEqual(['a1', 'a2'])
    const [a1, a2] = cards
    expect(a1?.tier).toBe('sonnet')
    expect(a1?.tasks).toEqual(['T3', 'T4'])
    expect(a1?.isActive).toBe(false)
    expect(elapsedMs(a1!, 999999)).toBe(60000)
    expect(fmtTokens(totalTokens(a1!.usage!.tokens))).toBe('2.5k tok')
    expect(a2?.tier).toBe('haiku')
    expect(a2?.tasks).toEqual(['T0'])
    expect(a2?.isActive).toBe(false)
  })

  test('live status comes from the events alone and counts to now while active', () => {
    const cards = mergeCards(
      { a9: { id: 'a9', type: 'Explore', description: 'look around', startedAt: 100, isActive: true } },
      emptySuperlite(),
      {},
    )
    expect(cards[0]?.isActive).toBe(true)
    expect(elapsedMs(cards[0]!, 5100)).toBe(5000)
    expect(cards[0]?.tier).toBeUndefined()
  })

  test('a launch row written after the prime stop still starts the card at the first stop', () => {
    const f = parseMetrics(
      [
        '{"ev":"stop","t":5000,"agent_id":"p1","type":"superlite:implementer","transcript":"x"}',
        '{"ev":"launch","t":6000,"agent_id":"p1","type":"superlite:implementer","desc":"Prime implementer A"}',
        '{"ev":"stop","t":65000,"agent_id":"p1"}',
      ].join('\n'),
    )
    const [p1] = mergeCards({}, { ...emptySuperlite(), ...f }, {})
    expect(p1?.startedAt).toBe(5000)
    expect(elapsedMs(p1!, 0)).toBe(60000)
  })

  test('tier from pool tier, model, then agent type', () => {
    expect(tierOf(undefined, 'claude-opus-5-5')).toBe('opus')
    expect(tierOf('haiku', 'claude-sonnet-5-5')).toBe('haiku')
    expect(tierOf(undefined, undefined, 'superlite:fixer-sonnet-high')).toBe('sonnet')
  })

  test('a run in another process: launched and not stopped, or a fresh transcript, is active', () => {
    const f = parseMetrics(METRICS)
    expect(fileActive(f, {}, 2000)).toEqual(['a2'])
    expect(fileActive(f, {}, 2000 + 16 * 60_000)).toEqual([])
    expect(fileActive(f, { a1: 100_000 }, 110_000).sort()).toEqual(['a1', 'a2'])
    expect(fileActive(f, { a1: 100_000 }, 130_000)).toEqual(['a2'])
    const files = { ...emptySuperlite(), ...f, active: fileActive(f, {}, 2000) }
    expect(mergeCards({}, files, {}).map(c => [c.id, c.isActive])).toEqual([['a1', false], ['a2', true]])
  })

  test('the ledger maps tasks to agents and ticks checkpointed tasks', () => {
    const led = parseLedger(
      '# Progress\n- sent T0 to implementer\n- checkpoint T0 3c5e20d2b5 skeleton\n- sent T3+T1+T5 to a1\r\n- sent T8, T7 to a2\n- sent R:T1+T3+T5 to rv1\n',
    )
    expect(led.ledger.length).toBe(5)
    expect(led.sent.rv1).toEqual(['T1', 'T3', 'T5'])
    expect(led.sent.a1).toEqual(['T3', 'T1', 'T5'])
    expect(led.sent.a2).toEqual(['T8', 'T7'])
    expect(led.done).toEqual(['T0'])
    const files = { ...emptySuperlite(), ...parseMetrics(METRICS), ...led, done: ['T3'] }
    const a1 = mergeCards({}, files, {}).find(c => c.id === 'a1')!
    expect(a1.tasks).toEqual(['T3', 'T1', 'T5'])
    expect(a1.doneTasks).toEqual(['T3'])
    expect(ledgerFor(led.ledger, a1)).toEqual(['sent T3+T1+T5 to a1', 'sent R:T1+T3+T5 to rv1'])
  })

  test('status: running green, aborted red, done grey', () => {
    const f = parseMetrics(
      [
        '{"ev":"launch","t":1000,"agent_id":"ok","desc":"T1 a"}',
        '{"ev":"stop","t":2000,"agent_id":"ok","last":"Status: DONE"}',
        '{"ev":"launch","t":1000,"agent_id":"blk","desc":"T2 b"}',
        '{"ev":"stop","t":2000,"agent_id":"blk","last":"Status: BLOCKED - no spec"}',
        '{"ev":"launch","t":1000,"agent_id":"lost","desc":"T3 c"}',
      ].join('\n'),
    )
    const files = { ...emptySuperlite(), ...f, active: fileActive(f, {}, 1000 + 16 * 60_000) }
    const by = Object.fromEntries(mergeCards({}, files, {}).map(c => [c.id, c.status]))
    expect(by).toEqual({ ok: 'done', blk: 'aborted', lost: 'aborted' })
    const live = mergeCards({ k: { id: 'k', type: 'x', description: 'y', isActive: false, isAborted: true } }, emptySuperlite(), {})
    expect(live[0]?.status).toBe('aborted')
  })
})

describe('graph', () => {
  const main = { title: 'Main session', meta: 'opus', status: 'running' as const }
  const nodes = [
    { id: 'c', title: 'Coordinate', meta: 'sonnet', tasks: '', status: 'running' as const },
    { id: 'i1', parent: 'c', title: 'T1 frontmatter', meta: 'sonnet', tasks: 'T1', status: 'done' as const },
    { id: 'i2', parent: 'c', title: 'T2 <markdown>', meta: 'haiku', tasks: 'T2', status: 'aborted' as const },
    { id: 'r', parent: 'nobody', title: 'Review', meta: 'sonnet', tasks: '', status: 'running' as const },
  ]

  test('each agent hangs under the agent that spawned it, else under the main session', () => {
    const order = treeOrder(main, nodes).map(r => [r.id, r.depth])
    expect(order).toEqual([['c', 0], ['i1', 1], ['i2', 1], ['r', 0]])
    const cyc = treeOrder(main, [
      { id: 'x', parent: 'y', title: 'x', meta: '', tasks: '', status: 'done' },
      { id: 'y', parent: 'x', title: 'y', meta: '', tasks: '', status: 'done' },
    ])
    expect(cyc.map(r => r.id).sort()).toEqual(['x', 'y'])
  })

  test('top to bottom: children sit below their parent, colored by status, text escaped', () => {
    const g = graphSvg(main, nodes, 2000)
    const ys = [...g.source.matchAll(/<rect x="(\d+)" y="(\d+)"/g)].map(m => Number(m[2]))
    expect(ys[0]).toBeLessThan(ys[1]!)
    expect(ys[1]).toBeLessThan(ys[2]!)
    expect(g.source).toContain(COLORS.aborted)
    expect(g.source).toContain(COLORS.done)
    expect(g.source).toContain('T2 &lt;markdown&gt;')
    expect(g.source).not.toContain('<markdown>')
    const narrow = graphSvg(main, Array.from({ length: 12 }, (_, i) => ({ id: `a${i}`, title: `T${i}`, meta: '', tasks: '', status: 'done' as const })), 600)
    expect(narrow.width).toBeLessThanOrEqual(600)
    expect(narrow.height).toBeGreaterThan(g.height)
  })

  test('each kind of agent gets its own edge style and a legend entry', () => {
    expect(roleOf('superlite:coordinator-sonnet')).toBe('coordinator')
    expect(roleOf('superlite:implementer-haiku')).toBe('implementer')
    expect(roleOf('superlite:package-reviewer')).toBe('reviewer')
    expect(roleOf('superlite:fixer-sonnet-high')).toBe('fixer')
    expect(roleOf('superlite:final-reviewer')).toBe('final')
    expect(roleOf('Explore')).toBe('other')
    const g = graphSvg(main, [
      { id: 'c', title: 'Coordinate', meta: '', tasks: '', status: 'running', type: 'superlite:coordinator-sonnet' },
      { id: 'r', parent: 'c', title: 'Review', meta: '', tasks: '', status: 'done', type: 'superlite:package-reviewer' },
      { id: 'f', parent: 'r', title: 'Fix', meta: '', tasks: '', status: 'aborted', type: 'superlite:fixer-sonnet' },
    ])
    for (const r of ['coordinator', 'reviewer', 'fixer'] as const) {
      expect(g.source).toContain(`data-role="${r}"`)
      expect(g.source).toContain(`>${ROLES[r].label}</text>`)
    }
    expect(g.source).toContain(`stroke-dasharray="${ROLES.reviewer.dash}"`)
    expect(g.source).not.toContain('>implementer</text>')
  })
})
