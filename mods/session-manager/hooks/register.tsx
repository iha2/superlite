import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Detail, Sess } from '../types'

const hub = atom({ plugin: 'session-manager', key: 'hub' } as const, '', { shape: 'hub-1' })
const active = atom({ plugin: 'session-manager', key: 'active' } as const, '', { shape: 'active-1' })
const defaultBase = atom({ plugin: 'session-manager', key: 'defaultBase' } as const, '', {
  shape: 'dbase-1',
})
const sessions = atom({ plugin: 'session-manager', key: 'sessions' } as const, [], {
  shape: 'sessions-1',
})
const selected = atom({ plugin: 'session-manager', key: 'selected' } as const, '', {
  shape: 'selected-1',
})
const detail = atom({ plugin: 'session-manager', key: 'detail' } as const, null, {
  shape: 'detail-1',
})
const note = atom({ plugin: 'session-manager', key: 'note' } as const, '', { shape: 'note-1' })

const PANE = 'session-manager'
const short = (p: string, root: string) => (root && p.startsWith(root) ? '.' + p.slice(root.length) : p)

// The registry lives in the main checkout; session worktrees share it.
const findHub = async ($: any): Promise<string> => {
  const r = await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'])
  const common = r.stdout.trim()

  return r.exitCode === 0 && common ? common.replace(/\/\.git\/?$/, '') : await $.session.cwd()
}

const refresh = async ($: any) => {
  const root = await findHub($)
  await update($, hub, () => root)
  try {
    const reg = JSON.parse(await $.fs.read(`${root}/.superlite/sessions.json`))
    const list: Sess[] = (reg.sessions ?? []).map((s: any) => ({
      slug: s.slug,
      branch: s.branch ?? '',
      worktree: s.worktree ?? '',
      store: s.store ?? '',
      status: s.status ?? '?',
      run: s.run ?? 'open',
      base: s.base ?? reg.base ?? '',
      stash: !!s.has_stash,
      createdAt: s.created_at ?? 0,
    }))
    await update($, sessions, () => list)
    await update($, active, () => reg.active ?? '')
    await update($, defaultBase, () => reg.base ?? '')
    await update($, note, () => '')
  } catch {
    await update($, sessions, () => [])
    await update($, note, () => 'No superlite session registry found (.superlite/sessions.json).')
  }
}

// Secondary metrics for the selected session: scratch store, dirty files, commits ahead of base.
const load = async ($: any, s: Sess) => {
  const cwd = s.worktree || (await read($, hub))
  const dirty = await $.process.run(['git', 'status', '--porcelain'], { cwd }).catch(() => null)
  const ahead = await $.process
    .run(['git', 'rev-list', '--count', `${s.base}..${s.branch}`], { cwd })
    .catch(() => null)
  const scratch = s.store
    ? await $.fs.list(s.store).then((l: any[]) => l.map(x => x.name)).catch(() => [])
    : []
  const d: Detail = {
    slug: s.slug,
    dirty: dirty?.stdout.split('\n').filter(Boolean) ?? [],
    scratch,
    ahead: ahead?.exitCode === 0 ? ahead.stdout.trim() : '?',
  }
  await update($, detail, () => d)
}

const select = async ($: any, s: Sess) => {
  await update($, selected, () => s.slug)
  await load($, s)
}

// `session use` parks or redirects. The agent root cannot be moved from a mod, so a
// worktree switch is announced in the chat in the form the superlite hooks already use.
const switchTo = async ($: any, s: Sess) => {
  const root = await read($, hub)
  const r = await $.process.run(
    [`${root}/skills/swarm/scripts/superlite-cli`, 'session', 'use', '--slug', s.slug, '--root', root],
    { cwd: root },
  )
  const redirect = r.stdout.split('\n').find((l: string) => l.startsWith('REDIRECT'))
  const text = redirect
    ? `superlite session: ${s.slug} moved to ${s.worktree} — move the agent root there`
    : r.exitCode === 0
      ? `superlite session: switched to ${s.slug} (branch ${s.branch})`
      : `superlite session: switching to ${s.slug} failed (${(r.stderr || r.stdout).trim().split('\n')[0]})`
  await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } })
  await update($, note, () => text)
  await refresh($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'sessions',
      description: 'Open the pane of superlite sessions, their worktrees and bases',
    })
    void $.ui.open({ id: PANE, title: 'Sessions' })
    await refresh($)

    return next(e)
  })

  on('command.run', { command: 'sessions' }, async $ => {
    await refresh($)
    await $.ui.open({ id: PANE, title: 'Sessions' })

    return { text: 'Sessions pane opened.' }
  })

  on('turn.complete', async ($, e, next) => {
    await refresh($)

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const root = await read($, hub)
    const list = await read($, sessions)
    const act = await read($, active)
    const sel = await read($, selected)
    const d = await read($, detail)
    const msg = await read($, note)
    const bases = [...new Set(list.map(s => s.base))]
    const chosen = list.find(s => s.slug === sel)

    return (
      <Box flexDirection="column">
        <Box>
          <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
          <Text dimColor> hub {root}</Text>
        </Box>
        {msg !== '' && <Text dimColor>{msg}</Text>}
        {list.length === 0 && <Text dimColor>No sessions.</Text>}
        {bases.map(b => (
          <Box key={`base-${b}`} flexDirection="column">
            <Text bold>{b || '(no base)'}</Text>
            {list
              .filter(s => s.base === b)
              .map(s => (
                <Box key={s.slug}>
                  <Text> {s.slug === act ? '●' : '○'} </Text>
                  <Button key={`sel-${s.slug}`} label={s.slug} onPress={() => select($, s)} />
                  <Text dimColor>
                    {' '}
                    {s.status} · run {s.run} · {s.worktree ? short(s.worktree, root) : 'host checkout'}
                    {s.stash ? ' · stash' : ''}
                  </Text>
                </Box>
              ))}
          </Box>
        ))}
        {chosen && (
          <Box flexDirection="column">
            <Text bold>{chosen.slug}</Text>
            <Text dimColor>branch {chosen.branch}</Text>
            <Text dimColor>worktree {chosen.worktree || '(host checkout)'}</Text>
            <Text dimColor>scratch {chosen.store}</Text>
            {d && d.slug === chosen.slug && (
              <Box flexDirection="column">
                <Text dimColor>ahead of {chosen.base}: {d.ahead} commits</Text>
                <Text dimColor>scratch: {d.scratch.length ? d.scratch.join(', ') : 'empty'}</Text>
                <Text dimColor>uncommitted files: {d.dirty.length}</Text>
              </Box>
            )}
            <Button
              key="switch"
              label={chosen.slug === act ? 'Active' : 'Switch agent here'}
              onPress={() => (chosen.slug === act ? undefined : switchTo($, chosen))}
            />
          </Box>
        )}
      </Box>
    )
  })
}
