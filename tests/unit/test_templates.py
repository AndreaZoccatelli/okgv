"""Tests for the `okgv init` scaffold presets (okgv/templates/<preset>/).

The package never imports these templates, so a core change (protocols,
validate_schema, validator serialization, the _meta fold) or a hand-edit can
break a preset with nothing to catch it. These tests load each preset the way
`init` would and run it through the same gates a real `submit` does, minus the
embedding model.

Four groups:
  1. every preset's structure.json folds without error;
  2. inventory invariants (registry == folders == `init --list`, required files);
  3. the function-calling preset stays byte-identical to example/;
  4. every preset's schema is valid against a representative entry + leaf.
"""

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import okgv.core as core
import okgv.templates
from okgv.commands.prompts import _PRESET_SCAFFOLD, _SHARED_SCAFFOLD, TEMPLATES
from okgv.main import cli
from okgv.protocols import EntrySchema, PropertyDefinition
from okgv.specs import build_specs

TEMPLATES_DIR = Path(okgv.templates.__file__).parent
REPO_ROOT = TEMPLATES_DIR.parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"

PRESETS = sorted(TEMPLATES)
# Presets that ship a worked project under examples/ (default is a blank scaffold).
EXAMPLE_PRESETS = [p for p in PRESETS if p != "default"]

# One representative (schema class, sample raw entry, leaf topic) per preset.
# The leaf is chosen to exercise the preset's _meta where it has one: qa's
# `basics` runs the difficulty narrowing, function-calling's `forecast` runs the
# validate_for_topic hook against the folded signature.
CASES = {
    "default": (
        "MyEntrySchema",
        {"text": "Water boils at 100C at sea level.", "category": "fact"},
        "my_topic/subtopic_a",
    ),
    "classification": (
        "UtteranceSchema",
        {"text": "I'd like a refund for last month.", "channel": "email"},
        "billing/refund",
    ),
    "qa": (
        "QASchema",
        {
            "question": "What is a basis of a vector space?",
            "answer": "A linearly independent spanning set.",
            "difficulty": "easy",
        },
        "algebra/linear_algebra/basics",
    ),
    "function-calling": (
        "ToolCallSchema",
        {
            "query": "forecast for Paris over the next 3 days",
            "function": "get_forecast",
            "arguments": {"location": "Paris", "days": 3},
            "difficulty": "easy",
        },
        "weather/forecast",
    ),
    "rag": (
        "RetrievalSchema",
        {
            "query": "how do I set up a vpn",
            "passage": "Open settings, add a VPN profile, and enter the server address.",
        },
        "networking/vpn/setup",
    ),
    "paraphrase": (
        "ParaphraseSchema",
        {"text": "The quick brown fox jumps over the lazy dog."},
        "seed_001",
    ),
}


def _load_schema_class(preset: str, class_name: str):
    """Load a preset's schema.py.txt as a module and return its schema class.

    A SourceFileLoader is set explicitly because the file has a .txt extension;
    `__file__` is the template path, so a schema that reads a sibling
    structure.json at import (function-calling) resolves it inside the preset.
    """
    path = TEMPLATES_DIR / preset / "schema.py.txt"
    mod_name = f"okgv_preset_{preset.replace('-', '_')}"
    loader = importlib.machinery.SourceFileLoader(mod_name, str(path))
    spec = importlib.util.spec_from_file_location(mod_name, str(path), loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return getattr(module, class_name)


@pytest.fixture
def runner():
    return CliRunner()


# ── 1. Structures fold ─────────────────────────────────────────────────────


@pytest.mark.parametrize("preset", PRESETS)
def test_structure_folds(preset):
    structure = json.loads((TEMPLATES_DIR / preset / "structure.json").read_text())
    specs = build_specs(structure)  # raises SpecError on malformed/contradictory _meta
    assert specs, f"{preset} structure declares no topics"


# ── 2. Inventory invariants ────────────────────────────────────────────────


def test_preset_folders_match_registry():
    folders = {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert folders == set(TEMPLATES)


def test_init_list_matches_registry(runner):
    result = runner.invoke(cli, ["init", "--list"])
    assert result.exit_code == 0
    listed = {t["name"] for t in json.loads(result.output)["templates"]}
    assert listed == set(TEMPLATES)


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_has_required_files(preset):
    for source_name, _ in _PRESET_SCAFFOLD:
        assert (TEMPLATES_DIR / preset / source_name).is_file(), f"{preset} missing {source_name}"


def test_shared_files_exist():
    for source_name, _ in _SHARED_SCAFFOLD:
        assert (TEMPLATES_DIR / source_name).is_file()
    assert (TEMPLATES_DIR / "cli-prompt.md").is_file()


def test_every_preset_has_a_case():
    # Guards #4: adding a preset without a CASES entry fails here, not silently.
    assert set(CASES) == set(TEMPLATES)


# ── 3. preset templates stay in sync with their worked examples/ ───────────

# Template file -> its counterpart under examples/<preset>/. schema.py and
# structure.json are the worked artifacts a user scaffolds, so they must stay
# byte-identical to the example. generation-guide.md is intentionally not here:
# the example carries a fuller, project-specific guide than the scaffold stub.
_DRIFT_PAIRS = [
    ("schema.py.txt", "config/schema.py"),
    ("structure.json", "config/structure.json"),
]


@pytest.mark.parametrize("preset", EXAMPLE_PRESETS)
@pytest.mark.parametrize(("template_name", "example_rel"), _DRIFT_PAIRS)
def test_preset_template_matches_example(preset, template_name, example_rel):
    example_path = EXAMPLES_DIR / preset / example_rel
    assert example_path.is_file(), f"examples/{preset}/{example_rel} is missing"
    template_text = (TEMPLATES_DIR / preset / template_name).read_text()
    assert template_text == example_path.read_text(), (
        f"okgv/templates/{preset}/{template_name} has drifted from examples/{preset}/{example_rel}"
    )


# ── 4. Schemas are valid against a representative entry + leaf ──────────────


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_schema_is_valid(preset):
    class_name, raw, topic = CASES[preset]
    schema = _load_schema_class(preset, class_name)

    # Conforms to the EntrySchema protocol and declares vector properties.
    assert isinstance(schema, EntrySchema)
    defs = schema.vector_property_definitions()
    assert defs and all(isinstance(d, PropertyDefinition) for d in defs)

    # Mirror submit's gates: build, validate against the leaf's folded spec, then
    # the structural schema check (key collisions + definition coverage).
    entry = core.build_entry(schema, raw)
    specs = build_specs(json.loads((TEMPLATES_DIR / preset / "structure.json").read_text()))
    core.validate_entry_topic(schema, entry, topic, specs.get(topic))
    core.validate_schema(
        schema,
        schema.metadata(entry),
        schema.graph_properties(entry),
        schema.vector_properties(entry),
    )

    text = schema.embedding_text(entry)
    assert isinstance(text, str) and text


# ── init plumbing (one e2e, not per-preset) ────────────────────────────────


def test_init_scaffolds_a_preset(runner):
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init", "-t", "qa"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["initialized"] and payload["template"] == "qa"
        for created in (
            ".env",
            "generation-guide.md",
            "config/schema.py",
            "config/structure.json",
            "config/validators.py",
            "config/__init__.py",
            "prompts/schema-guide.md",
            "prompts/reviewer-prompt.md",
            "prompts/structure-prompt.md",
        ):
            assert Path(created).exists(), f"init did not create {created}"
        # Per-preset content came from the qa preset, shared content from root.
        assert "QASchema" in Path("config/schema.py").read_text()
        assert "config.schema:QASchema" in Path(".env").read_text()


def test_init_unknown_template_errors(runner):
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init", "-t", "does-not-exist"])
        assert result.exit_code == 2
