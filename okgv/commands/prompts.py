"""Agent-facing prompt commands and project scaffolding."""

import click

from okgv.helpers import output
from okgv.validators import NEVER, narrow


def _effective_conjuncts(global_validator, topic_validators: list) -> list:
    """Conjunction of a field's global validator with its topic `entry` narrowings.

    Simplifies with ``narrow`` where possible; keeps conjuncts stacked when it
    cannot (or when a pair is provably empty, an authoring bug surfaced at
    ingest, never silently relaxed here). Rendering degrades toward more output,
    never weaker enforcement.
    """
    conjuncts = [global_validator] if global_validator is not None else []
    for v in topic_validators:
        merged = v
        kept = []
        for c in conjuncts:
            if getattr(c, "field", None) != getattr(merged, "field", object()):
                kept.append(c)
                continue
            result = narrow(c, merged)
            if result is None or result is NEVER:
                kept.append(c)
            else:
                merged = result
        kept.append(merged)
        conjuncts = kept
    return conjuncts


def _allowed_values(conjuncts: list):
    """Intersection of every ``OneOf`` valid set among the conjuncts, or None."""
    allowed = None
    for c in conjuncts:
        valid = getattr(c, "valid", None)
        if valid is not None:
            allowed = set(valid) if allowed is None else (allowed & set(valid))
    return allowed


def _constraint_text(conjuncts: list) -> str:
    """Human constraint phrase for a (possibly stacked) list of conjuncts."""
    phrases = [c.prompt().split(": ", 1)[1] for c in conjuncts]
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    return "all of the following must hold: " + "; ".join(phrases)


def _render_field(field: str, desc, validator, spec) -> str:
    """Render one Entry Fields bullet. With no topic narrowing (``spec`` None or
    no ``entry`` constraint on this field) the output is byte-identical to the
    flagless command."""
    topic_validators = spec.entry.get(field, []) if spec is not None else []

    if not topic_validators:
        # Unchanged legacy rendering.
        if isinstance(desc, tuple):
            label, options = desc
            constraint = f". {validator.prompt().split(': ', 1)[1]}" if validator else ""
            out = f"- **{field}**: {label}{constraint}\n"
            for opt, explanation in options.items():
                out += f"  - {opt}: {explanation}\n"
            return out
        parts = []
        if desc:
            parts.append(desc)
        if validator:
            parts.append(validator.prompt().split(": ", 1)[1])
        return f"- **{field}**: {'. '.join(parts)}\n"

    # Narrowed rendering: fold the topic's entry constraints into the global one.
    conjuncts = _effective_conjuncts(validator, topic_validators)
    marker = " (narrowed for this topic)"
    if any(not hasattr(c, "narrow") for c in conjuncts):
        marker += " (not machine-checked)"
    constraint = _constraint_text(conjuncts)
    allowed = _allowed_values(conjuncts)

    if isinstance(desc, tuple):
        label, options = desc
        if allowed is not None:
            options = {k: v for k, v in options.items() if k in allowed}
        suffix = f". {constraint}" if constraint else ""
        out = f"- **{field}**: {label}{suffix}{marker}\n"
        for opt, explanation in options.items():
            out += f"  - {opt}: {explanation}\n"
        return out

    parts = []
    if desc:
        parts.append(desc)
    if constraint:
        parts.append(constraint)
    return f"- **{field}**: {'. '.join(parts)}{marker}\n"


def _param_constraint(validators: list) -> str:
    if not validators:
        return "any value"
    return "; ".join(v.prompt().split(": ", 1)[1] for v in validators)


def _render_topic_constraints(topic: str, spec) -> str:
    """The appended section for constraints with no global field counterpart:
    function identity, argument signature, similarity scope."""
    if spec is None or spec.is_empty():
        return (
            f"\n## Topic constraints — {topic}\n\n"
            "No constraints are declared on this topic's path; only the global entry schema above applies.\n"
        )

    out = (
        f"\n## Topic constraints — {topic}\n\n"
        "These hold for entries filed here and have no global field counterpart:\n\n"
    )
    if spec.function is not None:
        out += f"- **function**: must be `{spec.function}`\n"
    if spec.required or spec.optional or spec.forbidden:
        out += "- **arguments**:\n"
        if spec.required:
            out += "  - required:\n"
            for name in sorted(spec.required):
                out += f"    - `{name}` — {_param_constraint(spec.required[name])}\n"
        if spec.optional:
            out += "  - optional:\n"
            for name in sorted(spec.optional):
                out += f"    - `{name}` — {_param_constraint(spec.optional[name])}\n"
        if spec.forbidden:
            out += f"  - forbidden: {', '.join(f'`{k}`' for k in sorted(spec.forbidden))}\n"
    out += f"- **similarity scope**: {spec.scope()}\n"
    return out


@click.command(name="cli-prompt")
@click.pass_context
def cli_prompt(ctx):
    """Print agent instructions for using the CLI."""
    from importlib.resources import files

    templates = files("okgv.templates")
    text = templates.joinpath("cli-prompt.md").read_text()

    click.echo(text)


@click.command(name="entry-prompt")
@click.option(
    "--topic",
    default=None,
    help="Render fields narrowed to this topic's effective spec, plus its argument signature.",
)
@click.pass_context
def entry_prompt(ctx, topic):
    """Print entry field descriptions and constraints for the agent.

    With --topic, the Entry Fields are rendered against the topic's folded
    effective spec: a field narrowed by node metadata shows only its allowed
    values (marked "(narrowed for this topic)") and an appended "Topic
    constraints" section lists the function name, argument signature, and
    similarity scope. Without --topic the output is unchanged.
    """
    validators = getattr(ctx.obj.schema, "validators", [])
    validator_map = {v.field: v for v in validators}
    descriptions = getattr(ctx.obj.schema, "field_descriptions", {})

    fields = dict.fromkeys([*descriptions.keys(), *validator_map.keys()])
    if not fields:
        click.echo("No field descriptions or validators defined in schema.")
        return

    spec = ctx.obj.effective_spec(topic) if topic else None

    if topic:
        text = f"# Entry Fields — {topic}\n\nEach entry under `{topic}` has the following fields:\n\n"
    else:
        text = "# Entry Fields\n\nEach entry in this knowledge base has the following fields:\n\n"

    for field in fields:
        text += _render_field(field, descriptions.get(field), validator_map.get(field), spec)

    balance_fields = getattr(ctx.obj.schema, "balance_fields", [])
    if balance_fields:
        text += "\n## Balancing\n\n"
        text += f"Ensure balanced coverage across these fields: {', '.join(balance_fields)}.\n"
        text += "Use `okgv topic-stats` to check current distribution within a specific topic.\n"

    if topic:
        text += _render_topic_constraints(topic, spec)

    click.echo(text)


@click.command()
def init():
    """Initialize current directory with okgv scaffold files."""
    from importlib.resources import files
    from pathlib import Path

    templates = files("okgv.templates")
    cwd = Path.cwd()
    created = []

    scaffold = [
        ("env.txt", ".env"),
        ("generation-guide.md", "generation-guide.md"),
        ("schema.py.txt", "config/schema.py"),
        ("structure.json", "config/structure.json"),
        ("schema-guide.md", "prompts/schema-guide.md"),
        ("reviewer-prompt.md", "prompts/reviewer-prompt.md"),
        ("structure-prompt.md", "prompts/structure-prompt.md"),
    ]

    for template_name, target_name in scaffold:
        target = cwd / target_name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            content = templates.joinpath(template_name).read_text()
            target.write_text(content)
            created.append(target_name)

    # Ensure config/ is importable as a Python package (needed for schema import)
    init_py = cwd / "config" / "__init__.py"
    if not init_py.exists() and (cwd / "config").exists():
        init_py.write_text("")
        created.append("config/__init__.py")

    if created:
        output({"initialized": True, "created": created})
    else:
        output({"initialized": False, "message": "All files already exist", "created": []})


commands = (cli_prompt, entry_prompt, init)
