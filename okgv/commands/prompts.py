"""Agent-facing prompt commands and project scaffolding."""

import click

from okgv.helpers import output


@click.command(name="cli-prompt")
@click.pass_context
def cli_prompt(ctx):
    """Print agent instructions for using the CLI."""
    from importlib.resources import files

    templates = files("okgv.templates")
    text = templates.joinpath("cli-prompt.md").read_text()

    click.echo(text)


@click.command(name="entry-prompt")
@click.pass_context
def entry_prompt(ctx):
    """Print entry field descriptions and constraints for the agent."""
    validators = getattr(ctx.obj.schema, "validators", [])
    validator_map = {v.field: v for v in validators}
    descriptions = getattr(ctx.obj.schema, "field_descriptions", {})

    fields = dict.fromkeys([*descriptions.keys(), *validator_map.keys()])
    if not fields:
        click.echo("No field descriptions or validators defined in schema.")
        return

    text = "# Entry Fields\n\nEach entry in this knowledge base has the following fields:\n\n"
    for field in fields:
        desc = descriptions.get(field)
        validator = validator_map.get(field)

        if isinstance(desc, tuple):
            label, options = desc
            constraint = f". {validator.prompt().split(': ', 1)[1]}" if validator else ""
            text += f"- **{field}**: {label}{constraint}\n"
            for opt, explanation in options.items():
                text += f"  - {opt}: {explanation}\n"
        else:
            parts = []
            if desc:
                parts.append(desc)
            if validator:
                parts.append(validator.prompt().split(": ", 1)[1])
            text += f"- **{field}**: {'. '.join(parts)}\n"

    balance_fields = getattr(ctx.obj.schema, "balance_fields", [])
    if balance_fields:
        text += "\n## Balancing\n\n"
        text += f"Ensure balanced coverage across these fields: {', '.join(balance_fields)}.\n"
        text += "Use `okgv topic-stats` to check current distribution within a specific topic.\n"

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
