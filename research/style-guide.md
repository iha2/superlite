# Style guide for the superlite docs site

Goal: short pages, clear enough that a reader knows exactly what to type and what happens. A picture comes first; words support it.

## Principles

- One idea per page. If a page needs two diagrams for two ideas, split it.
- Picture first: the first block after frontmatter is a diagram (mermaid or text).
- Start from the reader's goal, not the tool: "What you do" comes before mechanics (Diataxis: how-to guides are about goals, not tools).
- Assume the reader is competent: cut teaching and history; link to a reference page instead.
- Every guide page has a copyable example and says what the reader will see.
- Useful beats complete: leave out a fact rather than bury the main path.
- Keep guides (what to do) apart from reference (lookup tables). Do not mix them on one page.
- Facts only from the page's `site/research/facts-*.md`. A fact not there is not written.

## Page template (checkable)

Guide pages (groups start, flows, run, sessions), in this order:

1. Frontmatter: `title`, `description` (one sentence), `sidebar.order`.
2. A diagram block (```` ```mermaid ```` or ```` ```text ````), nothing before it.
3. `## What you do`
4. `## What superlite does`
5. `## Example`

- At most 300 prose words per page (text outside code fences and tables).
- Reference pages (reference, results): tables first, at most 300 prose words, a diagram where one helps.
- Flow pages embed `assets/flows/<mode>.txt` byte-identical in one ```` ```text ```` block.
- Titles name exactly what the page shows ("Sessions and worktrees", not "Overview").
- Every `/superlite:<x>`, `superlite-cli <x>` and `superlite:<x>` name must exist in the code.

## Voice

- Second person ("you say", "you approve"); superlite is the subject of "What superlite does".
- Present tense, active voice: "superlite opens a branch", not "a branch will be opened".
- Sentences under 20 words. One fact per sentence.
- Gloss a term in one line the first time it appears (session, worktree, orchestrator, seam check).
- Use the same word for the same thing on every page (always "session").
- Commands and file names in backticks, exactly as in the code.
- Steps are verbs: "Say what you want", "Approve the plan".
- Use tables for lookups, lists for 3 or more parallel items, prose for the rest.

## Diagram rules

- One diagram per concept; it must make sense without the text.
- `flowchart LR` for a pipeline left to right; `flowchart TD` for a hierarchy or decision.
- `sequenceDiagram` when order between actors matters (you, orchestrator, agents, git).
- Only those types. No gantt, state, class or others.
- At most 12 nodes. Past that, split the diagram or group steps.
- Labels are 1 to 4 words: verb first for steps ("Write plan"), noun for things ("Worktree").
- Quote any label with punctuation: `A["/superlite:start"]`.
- Show the human as a distinct node (`You`) and mark every point where the human decides (approve, validate, try).
- For sessions and worktrees, draw the real branch and folder names: `superlite/sessions/<topic>`.
- Use a `text` block for ASCII art only when a flow file already is one.

## Do and don't

- Do put the command a reader types in a code block; don't describe it in a sentence only.
- Do show real output or a real result in Example; don't invent output.
- Do say what is never done ("never pushes without you") where it builds trust.
- Don't write "simply", "just", "easily" or "obviously".
- Don't explain internals in a guide page; link to the run or reference page.
- Don't repeat the diagram in words. The text adds what the picture cannot (why, when).
- Don't use a diagram as decoration; every node must carry meaning.
- Don't leave a table cell longer than one sentence.

## Before and after (from the real README)

Before: "Each run starts its own session automatically (`superlite-cli session ensure`): a branch `superlite/sessions/<topic>` in your checkout, or a parallel worktree if another run is still open."
After: "You start a run. superlite opens a session: a branch `superlite/sessions/<topic>`. If another run is open, it uses a worktree (a second folder on its own branch) instead."

Before: "Feature mode: it researches the code (Graphify on a large codebase, never installed without your yes), writes a feature plan you validate, builds a small prototype in a sandbox inside your checkout for you to try, then ports it into your code with frozen interfaces and seam checks."
After: "You describe the change. superlite reads your code and writes a plan. You validate it. superlite builds a prototype in a sandbox for you to try. Then it ports the change into your code." The diagram carries the steps: Research, Plan, You validate, Prototype, You try, Port, Check seams.

Before: "The AI orchestrator plans, lints, dispatches and rules on open choices without stopping."
After: "Say 'I am away, decide yourself'. superlite plans, builds and decides open choices without asking you."

## Sources

- Diataxis, https://diataxis.fr/ and https://diataxis.fr/how-to-guides/ (fetched 2026-11-12): separate tutorial, how-to, reference, explanation; goal-first, adaptable, precisely titled guides.
- Starlight getting started, https://starlight.astro.build/getting-started/ (fetched 2026-11-12): short overview, ordered steps with one command each, callouts, "Next steps" links.
- Stripe docs home, https://docs.stripe.com/ (fetched 2026-11-12): quickstart first, goal-named entry links, then browse by product.
- Astro contribute page, https://docs.astro.build/en/contribute/ (fetched 2026-11-12): has no style rules; they live on a separate contributor site that was not read.
- Warp docs (docs.warp.dev) were not fetched. Their grouped-sidebar and prev/next patterns are taken from the plan text, not from reading the site.
- Before sentences: README.md in this repo.
