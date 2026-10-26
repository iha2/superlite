/**
 * Superlite plugin for OpenCode.ai
 *
 * Dual-compatible with OpenCode V1 and V2.
 *
 * V1 (opencode): loaded via named export SuperlitePlugin - provides config
 * hook for skills registration and experimental.chat.messages.transform for
 * bootstrap injection.
 *
 * V2 (opencode2): loaded via default export { id, setup } by PluginSupervisor.
 * setup() registers skills natively via ctx.skill.transform(), and injects
 * bootstrap context via ctx.session.hook("context").
 *
 * No external dependencies - pure JavaScript works in both V1 and V2 without
 * installing @opencode-ai/plugin or effect.
 */

import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Skills directory shared by V1 (config hook) and V2 (setup/ctx.skill.transform)
const superliteSkillsDir = path.resolve(__dirname, '../../skills');

// Marker used to avoid injecting the bootstrap twice into the same message.
const BOOTSTRAP_MARKER = 'superlite:using-superlite bootstrap';

// Simple frontmatter extraction. Handles plain `key: value` lines, quoted
// values (including quotes that close on an indented continuation line), YAML
// block scalar markers (`>`, `|`) with indented continuation lines, and CRLF
// line endings. Not a full YAML parser - nested maps flatten into their parent
// key's value, which is fine for the name/description fields consumed here.
const extractAndStripFrontmatter = (content) => {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!match) return { frontmatter: {}, content };

  const frontmatterStr = match[1];
  const body = match[2];
  const frontmatter = {};
  let lastKey = null;

  for (const rawLine of frontmatterStr.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    const colonIdx = line.indexOf(':');
    if (colonIdx > 0 && !/^\s/.test(line)) {
      const key = line.slice(0, colonIdx).trim();
      const value = line.slice(colonIdx + 1).trim();
      // Block scalar markers (>, |, optionally with +/- chomping) carry no
      // value themselves; the indented lines that follow do.
      frontmatter[key] = /^(>[+-]?|\|[+-]?)$/.test(value) ? '' : value;
      lastKey = key;
    } else if (lastKey !== null && line.trim() !== '') {
      // Continuation of a multi-line value: append rather than drop so long
      // descriptions survive parsing.
      frontmatter[lastKey] = `${frontmatter[lastKey]} ${line.trim()}`.trim();
    }
  }

  // A quoted value may close on a continuation line, so unquote only once
  // the value is fully assembled.
  for (const key of Object.keys(frontmatter)) {
    frontmatter[key] = frontmatter[key].replace(/^(["'])([\s\S]*)\1$/, '$2');
  }

  return { frontmatter, content: body };
};

// Tool mapping injected into the bootstrap, differentiated by host flavor.
// V1 (OpenCode 1.18.x) and V2 (OpenCode 2.0.4/2.0.7) expose different built-in
// tools, so each flavor's injection path picks its own constant below.

const AGENT_NOTE = `- \`Agent\` with \`subagent_type: superlite:<name>\` (\`implementer\`, \`implementer-haiku\`, \`package-reviewer\`, \`final-reviewer\`, \`fixer-sonnet\`, \`fixer-sonnet-high\` or \`fixer-opus\`) → OpenCode has no plugin agents. Dispatch the generic subagent described below with the full text of the plugin's \`agents/<name>.md\` (minus frontmatter) followed by the brief as its prompt, on the model tier named in that file (implementer, package-reviewer, fixer-sonnet, fixer-sonnet-high: mid-tier; implementer-haiku: cheapest tier; final-reviewer, fixer-opus: top tier; tests run via \`sl run_tests\`, not an agent). \`Agent\` with \`subagent_type: Explore\` → the \`explore\` agent.
- \`SendMessage\` to an earlier agent → continue that subagent's session if the tool supports it, otherwise dispatch a new one with the earlier report in its prompt
- \`AskUserQuestion\` → OpenCode's \`question\` tool when available, otherwise ask in plain text`;

// V1 built-ins: todowrite, task (subagent_type), skill, read, apply_patch,
// bash, grep, glob, webfetch.
export const V1_MAPPING = `**Tool Mapping for OpenCode:**
When skills request actions, substitute OpenCode equivalents:
- Create or update todos / plan → \`todowrite\`
- Dispatch a subagent → \`task\` with \`subagent_type: "general"\`
${AGENT_NOTE}
- Invoke a skill → OpenCode's native \`skill\` tool
- Read files → \`read\`
- Create, edit, or delete files → \`apply_patch\`
- Run shell commands → \`bash\`
- Search files → \`grep\`, \`glob\`
- Fetch a URL → \`webfetch\`

Use OpenCode's native \`skill\` tool to list and load skills.`;

// V2 built-ins: no todo tool at all; task → subagent (agent name in 'agent',
// continuation via sessionID); apply_patch → patch (patchText, same patch
// format); bash → shell.
export const V2_MAPPING = `**Tool Mapping for OpenCode:**
When skills request actions, substitute OpenCode equivalents:
- Create or update todos / plan → OpenCode v2 has no todo tool; track the plan in a markdown file (or the harness's plan facility) instead
- Dispatch a subagent → \`subagent\` with \`agent: "general"\` (give it \`description\` and \`prompt\`, optionally \`background\`; pass \`sessionID\` to continue a previous subagent, which is how \`SendMessage\` maps)
${AGENT_NOTE}
- Invoke a skill → OpenCode's native \`skill\` tool
- Read files → \`read\`
- Create, edit, or delete files → use \`patch\` with \`patchText\` when available; otherwise use \`write\` to create or overwrite files, \`edit\` for targeted changes, and \`shell\` for deletion
- Run shell commands → \`shell\` (\`command\`, \`workdir\`, \`timeout\`, \`background\`)
- Search files → \`grep\`, \`glob\`
- Fetch a URL → \`webfetch\`
- Search the web → \`websearch\`

Use OpenCode's native \`skill\` tool to list and load skills.`;

// Module-level cache for bootstrap content, keyed by tool mapping (host
// flavor). The SKILL.md file does not change during a session, so reading +
// parsing it once avoids redundant disk work on every agent step.
const _bootstrapCache = new Map(); // mapping -> bootstrap (null = file missing)

// Helper to generate bootstrap content (cached after first call per mapping)
const getBootstrapContent = (toolMapping) => {
  if (_bootstrapCache.has(toolMapping)) return _bootstrapCache.get(toolMapping);

  const skillPath = path.join(superliteSkillsDir, 'using-superlite', 'SKILL.md');
  if (!fs.existsSync(skillPath)) {
    _bootstrapCache.set(toolMapping, null);
    return null;
  }

  const fullContent = fs.readFileSync(skillPath, 'utf8');
  const { content } = extractAndStripFrontmatter(fullContent);

  _bootstrapCache.set(toolMapping, `${BOOTSTRAP_MARKER}

You have superlite. Its 'superlite:using-superlite' skill follows and is ALREADY LOADED; do not load it again with the skill tool. Load other skills with the skill tool.

${content}

${toolMapping}`);

  return _bootstrapCache.get(toolMapping);
};

// --- Task-subagent (child session) detection --------------------------------
//
// The bootstrap drives coordinator workflows (inventing, planning, the
// approval gate). Injecting it into task subagent sessions would make workers
// restart design cycles for work the parent already authorised. Detect child
// sessions structurally: a parentID on the session is the child signal on both
// flavors, so when the session carrying the message has a parentID we skip
// bootstrap injection. Skills stay registered for every session.

// sessionID -> is-child decision. parentID never changes for a session, so
// the result is cached until eviction. The cache is bounded: when full, drop
// the oldest quarter (Map iterates keys in insertion order).
const CHILD_SESSION_CACHE_MAX = 512;
const _childSessionCache = new Map();

const _cacheChildSession = (sessionID, isChild) => {
  if (_childSessionCache.size >= CHILD_SESSION_CACHE_MAX) {
    let toDrop = Math.ceil(CHILD_SESSION_CACHE_MAX / 4);
    for (const key of _childSessionCache.keys()) {
      if (toDrop-- <= 0) break;
      _childSessionCache.delete(key);
    }
  }
  _childSessionCache.set(sessionID, isChild);
};

const isChildSession = async (fetchSession, sessionID) => {
  if (!sessionID) return false; // unknown session: keep injecting
  if (_childSessionCache.has(sessionID)) return _childSessionCache.get(sessionID);

  let isChild = false;
  try {
    const result = await fetchSession(sessionID);
    // V1 returns a successful SDK envelope while V2 returns a direct session
    // record. Validate both shapes before classifying or caching the result.
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      throw new Error('Session lookup returned no usable record');
    }
    if (result.error != null || result.response?.ok === false) {
      throw new Error('Session lookup was unsuccessful');
    }
    const session = 'data' in result ? result.data : result;
    if (!session || typeof session !== 'object' || Array.isArray(session) || session.id !== sessionID) {
      throw new Error('Session lookup returned an invalid session identity');
    }
    if (session.parentID !== undefined &&
        (typeof session.parentID !== 'string' || session.parentID.length === 0)) {
      throw new Error('Session lookup returned an invalid parent identity');
    }
    isChild = session.parentID !== undefined;
  } catch (err) {
    // Fail open: on lookup errors keep injecting and do not cache, so a
    // transient failure can recover on the next step.
    console.error('[superlite] session lookup failed, treating session as top-level:', err);
    return false;
  }
  _cacheChildSession(sessionID, isChild);
  return isChild;
};

/**
 * V1 Plugin Function (named export + default.server)
 *
 * Used by V1 (OpenCode 1.x): discovered via named export scanning.
 * Provides: config hook (V1 skills registration) + bootstrap injection
 * (experimental.chat.messages.transform).
 */
export const SuperlitePlugin = async ({ client, directory }) => {
  return {
    // Inject skills path into live config so OpenCode discovers superlite skills
    // without requiring manual symlinks or config file edits.
    config: async (config) => {
      // V2: skills is a flat array - skip, setup() handles V2 skill registration
      if (Array.isArray(config.skills)) return;

      // V1: skills is { paths: [...] }
      config.skills = config.skills || {};
      config.skills.paths = config.skills.paths || [];
      if (!config.skills.paths.includes(superliteSkillsDir)) {
        config.skills.paths.push(superliteSkillsDir);
      }
    },

    // Inject bootstrap into the first user message of each top-level session.
    // A user message (not a system message) avoids repeating the text every
    // turn and avoids multiple system messages breaking some models.
    'experimental.chat.messages.transform': async (_input, output) => {
      const bootstrap = getBootstrapContent(V1_MAPPING);
      if (!bootstrap || !output.messages.length) return;
      const firstUser = output.messages.find(m => m.info.role === 'user');
      if (!firstUser || !firstUser.parts.length) return;

      // Guard: skip if first user message already contains bootstrap.
      if (firstUser.parts.some(p => p.type === 'text' && p.text.includes(BOOTSTRAP_MARKER))) return;

      // Never restart the coordinator workflow inside task subagent (child)
      // sessions. V1 passes no input to this hook, so take the sessionID from
      // the message record itself.
      if (client && await isChildSession(
        (id) => client.session.get({ path: { id } }),
        firstUser.info.sessionID,
      )) return;

      const ref = firstUser.parts[0];
      firstUser.parts.unshift({ ...ref, type: 'text', text: bootstrap });
    }
  };
};

/**
 * V2 Setup Function (default.setup)
 *
 * Called by V2 PluginSupervisor. Performs two things:
 *
 * 1. Registers every skills/<name>/SKILL.md as a native Skill.Info object
 *    via ctx.skill.transform((draft) => draft.add(info)). add() decodes plain
 *    objects against the host's Skill.Info schema (OpenCode 2.0.4 contract):
 *    { id, name, description?, autoinvoke?, path, content }.
 * 2. Injects bootstrap context via ctx.session.hook("context"), the V2
 *    equivalent of V1's experimental.chat.messages.transform.
 */
async function setup(ctx) {
  // V1 also invokes default.setup, but with a V1-shaped ctx that lacks the
  // skill/session domains. Detect it and return quietly - V1 is served
  // entirely by the SuperlitePlugin named export.
  if (!ctx || !ctx.skill || typeof ctx.skill.transform !== 'function' || !ctx.session || typeof ctx.session.hook !== 'function') {
    return;
  }

  // 1. Register skills (one transform; one draft.add per skill)
  try {
    const skills = [];
    if (fs.existsSync(superliteSkillsDir)) {
      for (const entry of fs.readdirSync(superliteSkillsDir, { withFileTypes: true })) {
        if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
        const skillPath = path.join(superliteSkillsDir, entry.name, 'SKILL.md');
        if (!fs.existsSync(skillPath)) continue;
        const { frontmatter, content } = extractAndStripFrontmatter(fs.readFileSync(skillPath, 'utf8'));
        skills.push({
          id: entry.name,
          name: frontmatter.name || entry.name,
          ...(frontmatter.description ? { description: frontmatter.description } : {}),
          path: skillPath,
          content,
        });
      }
    }
    await ctx.skill.transform((draft) => {
      // draft.add() throws synchronously on a schema mismatch, and a throw
      // escaping this callback hard-disables the whole plugin. Contain
      // failures per skill so one rejected payload skips only that skill.
      for (const skill of skills) {
        try {
          draft.add(skill);
        } catch (err) {
          console.error(`[superlite] skill "${skill.id}" rejected by host, skipping:`, err);
        }
      }
    });
  } catch (err) {
    // Never break plugin activation: one failing plugin takes down the whole
    // V2 generation.
    console.error('[superlite] skill registration failed:', err);
  }

  // 2. Inject bootstrap into first user message via V2 session context hook
  try {
    await ctx.session.hook('context', async (event) => {
      try {
        const bootstrap = getBootstrapContent(V2_MAPPING);
        if (!bootstrap || !event.messages || !event.messages.length) return;
        const firstUser = event.messages.find(m => m.role === 'user');
        if (firstUser && (!firstUser.content || !firstUser.content.length)) return;
        if (firstUser?.content.some(p => p.type === 'text' && p.text && p.text.includes(BOOTSTRAP_MARKER))) return;

        // The context event carries the sessionID directly. Skip the
        // coordinator bootstrap when this prompt belongs to a task subagent
        // (child) session. Skills registered above stay available to workers.
        if (typeof ctx.session.get === 'function' && await isChildSession(
          (id) => ctx.session.get({ sessionID: id }),
          event.sessionID,
        )) return;

        // Native compaction can leave only an opaque checkpoint. Keep it
        // intact and append the transient bootstrap as a user message.
        if (firstUser) {
          firstUser.content.unshift({ type: 'text', text: bootstrap });
        } else {
          event.messages.push({ role: 'user', content: [{ type: 'text', text: bootstrap }] });
        }
      } catch (err) {
        // Never let hook callback errors break the request pipeline.
        console.error('[superlite] context hook failed:', err);
      }
    });
  } catch (err) {
    console.error('[superlite] session hook registration failed:', err);
  }
}

/**
 * Default Export: { id, server, setup }
 *
 * V2 PluginSupervisor reads { id, setup }.
 * V1 reads named export SuperlitePlugin.
 * server() is exported for V1 compatibility.
 */
export default {
  id: 'superlite',
  server: SuperlitePlugin,
  setup,
};
