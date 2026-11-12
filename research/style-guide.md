# Style guide for the superlite docs site

Goal: pages that teach. A reader leaves with a mental model, has seen an example, and knows what to type. A TL;DR sits at the top for the reader in a hurry. Understanding is the goal, not brevity.

## Page shape (every page except `index.mdx`)

1. Frontmatter: `title`, `description` (one sentence), `sidebar.order`.
2. **TL;DR first**: a Starlight aside of at most 60 words (1 to 3 short sentences or bullets). In `.mdx`, `import` lines may come before it.

   ```md
   :::tip[TL;DR]
   A session is one piece of work on its own branch. superlite opens one for every run and closes it when you merge.
   :::
   ```

3. **A diagram before the first `##` heading**: mermaid, or on flow pages the stored flow screen in a ```` ```text ```` block.
4. Sections that teach. Suggested: `## How it works` (mental model and why), `## Example` or `## Example: <scenario>` (required on guide pages: a realistic walkthrough), `## Good to know` (edge cases, what is never done), `## Next`.
5. At most 900 prose words (text outside code fences and tables). Aim for 300 to 600.

Guide pages are the groups `start`, `flows`, `run` and `sessions`. Reference pages (`reference`, `results`) need the TL;DR and can lead with tables, but add a diagram or a short example where it helps.

What the checker enforces (`site/checks/check_pages.py`):

- the first non-blank body line (lines starting `import ` skipped) is exactly `:::tip[TL;DR]`, closed by a `:::` line, with at most 60 words inside;
- on guide pages, a ```` ```mermaid ```` or ```` ```text ```` block before the first `## ` heading, and a heading starting `## Example`;
- every mermaid block starts with `flowchart`, `sequenceDiagram`, `stateDiagram-v2` or `gitGraph`;
- at most 900 prose words;
- every `/superlite:<x>`, `superlite-cli <x>` and `superlite:<x>` name exists in the code; every link resolves; flow pages hold `assets/flows/<mode>.txt` byte-identical in one ```` ```text ```` block.

## Teaching rules

- **Mental model first.** Say what the thing is in plain words, with an analogy when it helps: a worktree is "a second desk with its own copy of the project, on its own branch".
- **Not repetitive.** Each concept has one home page where it is explained in full. Elsewhere, link to it with one clause.
  - Homes: session in `sessions/sessions`; worktree in `sessions/worktrees`; sandbox in `sessions/sandbox`; session lifecycle in `sessions/lifecycle`; orchestrator in `run/orchestrators`; plan and approval in `run/plan-and-approval`; agents, tiers and parallel waves in `run/parallel-build`; reviews and fix tiers in `run/reviews-and-fixes`; report in `run/report`; each flow in `flows/<mode>`.
  - `start/concepts` is the map: one or two lines per concept, one overall diagram, a link to each home. It maps; it does not explain.
- **Examples everywhere.** Every guide page has at least one walkthrough: what you type, what you see, what you answer, what happens. Show it as a ```` ```text ```` transcript:

  ```text
  you        /superlite:feature Add CSV export
  superlite  reads the code and writes a code map
  superlite  plan ready: docs/plans/2026-11-12-csv-export.review.md
  you        approve
  ```

  Lines superlite prints must match what the code prints; read the script or skill. Where the exact wording is not in the code, keep the line plainly descriptive instead of inventing output.
- **Illustrations.** Use Starlight components where they make things visible (the page must be `.mdx`; import only what you use from `@astrojs/starlight/components`).
  - `<FileTree>` for folders: `.superlite/`, worktrees, sandboxes, plans.
  - `<Steps>` for ordered things the reader does.
  - `<Tabs>` for human vs AI orchestrator, or Claude Code vs other harnesses.
  - Asides (`:::note`, `:::caution`, `:::tip[...]`) for promises ("never pushes without you") and gotchas.
- **Translate internal tokens.** When the reader must react to a printed line (for example `ASK move`), quote it once inside an example and say in plain words what it asks and what each answer does. Do not build prose out of tokens.
- Facts only from the code and the page's `site/research/facts-*.md`. When they disagree, the code wins. A fact not there is not written.

## Diagram rules

- Allowed mermaid types: `flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `gitGraph`. No gantt, class, pie or `graph`.
  - `gitGraph` for branches (`main`, `superlite/sessions/<topic>`, a feature `-int` branch).
  - `stateDiagram-v2` for the session lifecycle.
  - `sequenceDiagram` for who talks to whom (You, superlite, agents, git).
  - `flowchart LR` for a pipeline left to right; `flowchart TD` for a hierarchy or decision.
- About 12 nodes at most, short labels (verb first for steps, noun for things). Quote any label with punctuation: `A["/superlite:start"]`.
- Show the human as a distinct node (`You`) and mark every point where the human decides (approve, validate, try).
- For sessions and worktrees, draw the real branch and folder names: `superlite/sessions/<topic>`.
- A diagram must make sense on its own. The text adds what the picture cannot (why, when) and does not repeat it. Every node carries meaning; no decoration.
- Use a `text` block for ASCII art only when a flow file already is one.

## Voice

- Second person ("you say", "you approve"). Present tense, active voice: "superlite opens a branch", not "a branch will be opened".
- Short sentences, one fact each.
- No "simply", "just", "easily" or "obviously".
- Gloss a term in one line the first time it appears, then link to its home page.
- Use the same word for the same thing on every page (always "session").
- Commands and file names in backticks, exactly as in the code. Links are site-absolute: `/superlite/<group>/<page>/`.
- Titles name exactly what the page shows ("Sessions and worktrees", not "Overview").
- Use tables for lookups, lists for 3 or more parallel items, prose for the rest. Keep a table cell to one sentence.

## Do and don't

- Do put the command a reader types in a code block; do not describe it in a sentence only.
- Do say what is never done ("never pushes without you") where it builds trust.
- Do show real output or a real result in the example; do not invent output.
- Do not explain internals twice; link to the page that owns the concept.

## Before and after (from the real README)

Before: "Each run starts its own session automatically (`superlite-cli session ensure`): a branch `superlite/sessions/<topic>` in your checkout, or a parallel worktree if another run is still open."
After: "You start a run. superlite opens a session: a branch `superlite/sessions/<topic>`. If another run is open, it uses a worktree (a second folder on its own branch) instead."

Before: "The AI orchestrator plans, lints, dispatches and rules on open choices without stopping."
After: "Say 'I am away, decide yourself'. superlite plans, builds and decides open choices without asking you."

## Sources

- Diataxis, https://diataxis.fr/ and https://diataxis.fr/how-to-guides/ (fetched 2026-11-12): separate tutorial, how-to, reference, explanation; goal-first, precisely titled guides.
- Starlight getting started, https://starlight.astro.build/getting-started/ (fetched 2026-11-12): short overview, ordered steps, callouts, "Next steps" links.
- Stripe docs home, https://docs.stripe.com/ (fetched 2026-11-12): quickstart first, goal-named entry links.
- Astro contribute page, https://docs.astro.build/en/contribute/ (fetched 2026-11-12): has no style rules; they live on a separate contributor site that was not read.
- Warp docs (docs.warp.dev) were not fetched.
- Before sentences: README.md in this repo.
