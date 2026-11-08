# OpenAI public directory submission

Status: preparation in progress; not submitted or approved.
Audited source: 9747f27e49ed3d86957697540adcf328304e9bed (3.2.0).

## Distribution target

Use the public plugin submission portal at https://platform.openai.com/plugins.
Adding this repository with `codex plugin marketplace add iha2/superlite` registers
its custom catalog; it does not publish it in the public directory.

Superlite calls local Bash/Python helpers and operates on a local checkout, so its
Codex manifest declares `requires_local_executor: true`. It has no MCP server;
do not introduce a local MCP service or claim remote/web compatibility just to
submit it.

## Changes prepared

- Use provider-neutral package description.
- Keep subtitle within the public directory's 30-character limit.
- Remove the empty lifecycle-hooks declaration.
- Add the repository issues page as the support URL.
- Remove GitHub's platform privacy/terms URLs: these are not Superlite policies.
  Skills-only submission does not require all four MCP listing URLs. Add actual
  publisher policies if the portal or chosen distribution requires them.

## Remaining release gates

1. Choose the verified developer identity and OpenAI organization/project. The
   submitter must be an owner or hold Apps Management Write. Do not invent a
   publisher identity, accept legal terms, or choose country availability silently.
2. Convert reusable `commands/*.md` and `agents/*.md` procedures into skills,
   update cross-references, and verify their package-relative helper paths. The
   Codex mapping currently reads root agent files, and `using-superlite` points to
   `/superlite:start` and `superlite:proof` (proof is not an existing skill).
   Simply zipping the repository does not resolve these compatibility gaps.
3. Replace Claude-specific tool instructions where they are not product-specific.
   Confirm model selection and delegation against the target session's tools.
4. Resolve automatic bootstrap without lifecycle hooks. AGENTS.md requires every
   harness to load `using-superlite` at session start and forbids per-session opt-in
   as acceptance. The public directory does not accept lifecycle hooks. An
   onboarding skill is a setup flow, not proof of every-session bootstrap. Verify
   supported automatic activation or agree on a separately scoped distribution
   behavior before claiming this gate passes.
5. Build an allowlisted ZIP containing only the chosen manifest, skills and their
   required helpers/references/assets, plus license attribution. Exclude other
   harness manifests, lifecycle hooks, benchmarks, git metadata and local state.
   Do not remove required procedures while stripping unsupported components.
6. In a clean desktop installation, send `Let's make a react todo list` and verify
   inventing runs before code, as AGENTS.md requires. Also exercise planning,
   delegation/review, testing, diagnosis and shipping. Record actual evidence;
   existing unit tests do not establish host integration.
7. Upload the ZIP as a draft, resolve portal scans, review listing/identity and
   submit for OpenAI review. Publish after approval. No approval or listing is
   guaranteed by local checks.

## Sources

- https://developers.openai.com/plugins/deploy/submission
- https://developers.openai.com/plugins/guides/submit-claude-plugin
- https://developers.openai.com/plugins/deploy/desktop-only

Documentation checked during preparation. Recheck before submission.
