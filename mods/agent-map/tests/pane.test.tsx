import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const SURFACES = ['terminal', 'desktop'] as const
const PLUGIN = 'superlite-agents'
const PANE = 'superlite-agents'

const props = (bodyColumns = 100) => ({
  title: 'Agent map',
  isFocused: false,
  bodyColumns,
  placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 40 },
  view: {},
})

const FILES: Record<string, string> = {
  '.superlite/metrics.jsonl': [
    '{"ev":"launch","t":1000,"agent_id":"r1","type":"superlite:implementer","model":"claude-sonnet-5-5","desc":"T3 templates"}',
    '{"ev":"stop","t":61000,"agent_id":"r1","type":"superlite:implementer","transcript":"C:/t/agent-r1.jsonl","last":"Status: DONE"}',
  ].join('\n'),
  '.superlite/pool.json': '{"agents":[{"id":"r1","tasks":["T3","T4"],"tier":"sonnet"}]}',
  'C:/t/agent-r1.jsonl':
    '{"type":"assistant","timestamp":"2026-10-20T10:00:00Z","message":{"id":"m1","usage":{"input_tokens":100,"output_tokens":400,"cache_read_input_tokens":1000,"cache_creation_input_tokens":0},"content":[]}}',
}

const RUN = { isFullscreen: true, columns: 160 }

/** The world beneath the plugin: files from `files` by path suffix (anything else is missing), a pane that opens. */
function world(on: On, files: Record<string, string>) {
  mock.clock(on, { now: 1_000_000 })
  on('fs.read', ($, e) => {
    const path = e.path.replace(/\\/g, '/')
    const hit = Object.keys(files).find(k => path === k || path.endsWith(`/${k}`))
    return hit === undefined ? { deny: `ENOENT ${e.path}` } : { value: files[hit]! }
  })
  on('fs.stat', ($, e) => {
    const path = e.path.replace(/\\/g, '/')
    const hit = Object.keys(files).find(k => path === k || path.endsWith(`/${k}`))
    return hit === undefined
      ? { deny: `ENOENT ${e.path}` }
      : { value: { kind: 'file' as const, size: files[hit]!.length, mtimeMs: 0, isLink: false } }
  })
  on('ui.open', () => ({ value: { isPlaced: true as const } }))
  on('classic.SubagentStart', () => ({}))
  on('classic.SubagentStop', () => ({}))
}

async function svgSource(ui: { find: (q: { type?: string }) => Promise<{ props: Record<string, unknown> } | undefined> }) {
  return String((await ui.find({ type: 'Svg' }))?.props.source ?? '')
}

async function dotColor(ui: { findAll: (q: { type?: string; text?: string | RegExp }) => Promise<{ props: Record<string, unknown> }[]> }) {
  return (await ui.findAll({ type: 'Text', text: /^●$/ })).map(t => t.props.color)
}

describe('live status', () => {
  for (const surface of SURFACES) {
    test(`SubagentStart turns the card green, SubagentStop grey (${surface})`, async ($, on) => {
      world(on, {})
      await $.classic.SubagentStart({ agent_id: 'a1', agent_type: 'superlite:implementer' })
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: PANE, props: props() })

      expect(await ui.find({ key: 'card-a1' })).toBeDefined()
      expect((await dotColor(ui)).at(-1)).toBe('success')
      if (surface === 'desktop') expect(await svgSource(ui)).toContain('#22a35a')

      await $.classic.SubagentStop({
        agent_id: 'a1',
        agent_type: 'superlite:implementer',
        stop_hook_active: false,
        agent_transcript_path: '',
        last_assistant_message: 'Status: DONE',
      })
      expect((await dotColor(ui)).at(-1)).toBe('inactive')
      if (surface === 'desktop') expect(await svgSource(ui)).not.toContain('#22a35a')
    })
  }
})

describe('superlite records', () => {
  for (const surface of SURFACES) {
    test(`metrics rows fill the card; a press shows its details (${surface})`, async ($, on) => {
      world(on, FILES)
      const ran = await $.command.run({ command: 'superlite-agents', args: '', origin: { kind: 'composer' }, presentation: RUN })
      expect(ran).toEqual({ text: 'Agent map opened (.superlite).' })

      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: PANE, props: props() })
      const card = await ui.find({ key: 'card-r1' })
      expect(card?.text).toContain('T3 templates')
      if (surface === 'desktop') {
        const svg = await svgSource(ui)
        expect(svg).toContain('T3 templates')
        expect(svg).toContain('sonnet · 1m 0s · 1.5k tok')
        expect(svg).toContain('T3 T4')
      } else {
        expect(card?.text).toContain('sonnet · 1m 0s · 1.5k tok · T3 T4')
        expect(await ui.find({ key: 'box-r1' })).toBeDefined()
      }
      expect((await dotColor(ui)).at(-1)).toBe('inactive')

      await ui.press({ key: 'card-r1' })
      expect(await ui.find({ type: 'Text', text: 'done (from the run files)' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: 'main session' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: 'in 100, out 400' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: 'Status: DONE' })).toBeDefined()

      await ui.press({ key: 'back' })
      expect(await ui.find({ key: 'card-r1' })).toBeDefined()
    })
  }

  test('missing files: no crash, live events only', async ($, on) => {
    world(on, {})
    const ran = await $.command.run({ command: 'superlite-agents', args: '', origin: { kind: 'composer' }, presentation: RUN })
    expect(ran).toEqual({ text: 'Agent map opened (no .superlite records here; live events only).' })
    const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', component: 'Pane', requestId: PANE, props: props(40) })
    expect(await ui.find({ type: 'Text', text: 'no subagents yet' })).toBeDefined()
  })

  test('the open_agent_map tool opens the pane on a project, read-only', async ($, on) => {
    world(on, FILES)
    const ran = await $.tool.call({ tool: 'mcp__superlite-agents__open_agent_map', tool_use_id: 'tu1', dir: '.' })
    expect(ran).toMatchObject({ text: 'Agent map opened (./.superlite).' })
  })

  test('a project dir argument reads that project', async ($, on) => {
    world(on, { 'C:/runs/q2/.superlite/metrics.jsonl': FILES['.superlite/metrics.jsonl']! })
    const ran = await $.command.run({ command: 'superlite-agents', args: 'C:/runs/q2', origin: { kind: 'composer' }, presentation: RUN })
    expect(ran).toEqual({ text: 'Agent map opened (C:/runs/q2/.superlite).' })
  })
})
