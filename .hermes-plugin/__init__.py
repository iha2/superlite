import os
import re
from pathlib import Path

BOOTSTRAP_MARKER = "superlite:using-superlite bootstrap for hermes"


def _skills_dir() -> str:
    """Locate the skills/ tree for either supported install layout.

    - git-clone install (`hermes plugins install iha2/superlite`): the plugin
      dir is the repo root, so `.hermes-plugin/` and `skills/` are siblings and
      this module resolves `../skills`.
    - flattened install (plugin files copied to the plugin dir root): `skills/`
      sits next to this module.

    Raises loudly when neither matches - a bootstrap that silently skips is how
    a broken install masquerades as a working one.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    candidates = (
        os.path.realpath(os.path.join(here, "..", "skills")),
        os.path.realpath(os.path.join(here, "skills")),
    )
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "using-superlite", "SKILL.md")):
            return cand
    raise RuntimeError(
        "superlite plugin: cannot find the skills/ tree "
        f"(looked at {candidates}). Reinstall with "
        "`hermes plugins install iha2/superlite`."
    )


def _strip_frontmatter(content: str) -> str:
    match = re.match(r"^---\n[\s\S]*?\n---\n([\s\S]*)$", content)
    return (match.group(1) if match else content).strip()


def _build_bootstrap(skills_dir: str) -> str:
    with open(
        os.path.join(skills_dir, "using-superlite", "SKILL.md"),
        encoding="utf-8",
    ) as f:
        body = _strip_frontmatter(f.read())

    tools_path = os.path.join(
        skills_dir, "using-superlite", "references", "hermes-tools.md"
    )
    with open(tools_path, encoding="utf-8") as f:
        tool_mapping = f.read().strip()

    agents_dir = os.path.realpath(os.path.join(skills_dir, "..", "agents"))

    return (
        f"{BOOTSTRAP_MARKER}\n\n"
        f"You have superlite. Its 'superlite:using-superlite' skill follows and "
        f"is already loaded for this Hermes session. Follow it now. "
        f"Do not try to load using-superlite again.\n\n"
        f"{body}\n\n"
        f"## Loading Superlite Skills on Hermes\n\n"
        f"Superlite skills are registered with Hermes' native skill loader: "
        f'invoke one with `skill_view("superlite:skill-name")` '
        f'(for example `skill_view("superlite:inventing")`). '
        f"If a namespaced lookup returns 'not found', read the skill file "
        f"directly instead:\n"
        f'`read_file("{skills_dir}/skill-name/SKILL.md")`\n\n'
        f"The superlite skills directory is: `{skills_dir}`\n"
        f"The superlite agent definitions directory is: `{agents_dir}`\n\n"
        f"{tool_mapping}\n"
    )


def register(ctx):
    skills_dir = _skills_dir()
    bootstrap = _build_bootstrap(skills_dir)

    # Register every skill with Hermes' native loader so skill_view can load
    # them on demand. Standard markdown; no conversion (plugin guide).
    # register_skill requires a pathlib.Path - a str raises AttributeError and
    # hermes silently disables the whole plugin.
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if os.path.isfile(skill_md):
            ctx.register_skill(name, Path(skill_md))

    # pre_llm_call returning {"context": ...} is the documented injection path
    # (on_session_start return values are ignored, and ctx.inject_message
    # refuses from that hook). The context is appended to the first turn's user
    # message.
    def pre_llm_call(
        session_id=None,
        user_message=None,
        conversation_history=None,
        is_first_turn=None,
        model=None,
        platform=None,
        **kwargs,
    ):
        if is_first_turn:
            return {"context": bootstrap}
        return None

    ctx.register_hook("pre_llm_call", pre_llm_call)
