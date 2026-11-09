export type Sess = {
  slug: string
  branch: string
  worktree: string
  store: string
  status: string
  run: string
  base: string
  stash: boolean
  createdAt: number
}
export type Detail = { slug: string; dirty: string[]; scratch: string[]; ahead: string }

declare module 'claude-code' {
  interface PluginState {
    'session-manager': {
      hub: Shaped<string>
      active: Shaped<string>
      defaultBase: Shaped<string>
      sessions: Shaped<Sess[]>
      selected: Shaped<string>
      detail: Shaped<Detail | null>
      note: Shaped<string>
    }
  }
}
