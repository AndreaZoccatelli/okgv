"""Tests for `entry-prompt`, including the M9 --topic narrowed rendering."""

import json

import pytest
from click.testing import CliRunner

from okgv.main import cli
from okgv.session import Session
from okgv.validators import NotEmpty, OneOf, register


@pytest.fixture
def runner():
    return CliRunner()


class _PromptSchema:
    """Minimal schema exercising both plain and tuple field descriptions."""

    entry_class = object
    validators = [NotEmpty("query"), OneOf("difficulty", {"easy", "medium", "hard"})]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "query": "the user request",
        "difficulty": (
            "how hard the item is",
            {"easy": "explicit", "medium": "some inference", "hard": "ambiguous"},
        ),
    }


def _session(tmp_path, monkeypatch, structure):
    sfile = tmp_path / "structure.json"
    sfile.write_text(json.dumps(structure))
    monkeypatch.setenv("OKGV_STRUCTURE", str(sfile))
    # db_path intentionally points at a non-existent file so the session-start
    # consistency check is a no-op and no DB is created.
    return Session(schema=_PromptSchema(), db_path=tmp_path / "okgv.db")


def _run(runner, session, *args):
    result = runner.invoke(cli, ["entry-prompt", *args], obj=session)
    assert result.exit_code == 0, result.output
    return result.stdout


class TestNoFlag:
    def test_flagless_output_unchanged(self, runner, tmp_path, monkeypatch):
        session = _session(tmp_path, monkeypatch, {"t": {}})
        out = _run(runner, session)
        assert out.startswith("# Entry Fields\n\nEach entry in this knowledge base")
        assert "- **difficulty**: how hard the item is. must be one of ['easy', 'hard', 'medium']\n" in out
        assert "  - easy: explicit\n" in out
        assert "  - medium: some inference\n" in out
        assert "  - hard: ambiguous\n" in out
        # No topic machinery leaks into the flagless output.
        assert "narrowed for this topic" not in out
        assert "Topic constraints" not in out

    def test_topic_without_entry_constraints_matches_global_fields(self, runner, tmp_path, monkeypatch):
        # A topic that constrains only arguments leaves the Entry Fields lines
        # identical to the global rendering (only the header differs).
        structure = {"t": {"_meta": {"function": "f", "required": {"x": {"type": "not_empty", "field": "x"}}}}}
        session = _session(tmp_path, monkeypatch, structure)
        out = _run(runner, session, "--topic", "t")
        assert out.startswith("# Entry Fields — t\n")
        assert "- **difficulty**: how hard the item is. must be one of ['easy', 'hard', 'medium']\n" in out
        assert "narrowed for this topic" not in out


class TestNarrowedFields:
    def test_oneof_narrowed_filters_options_and_marks(self, runner, tmp_path, monkeypatch):
        difficulty = {"type": "one_of", "field": "difficulty", "valid": ["hard"]}
        structure = {"t": {"_meta": {"function": "f", "entry": {"difficulty": difficulty}}}}
        session = _session(tmp_path, monkeypatch, structure)
        out = _run(runner, session, "--topic", "t")
        assert "- **difficulty**: how hard the item is. must be one of ['hard'] (narrowed for this topic)\n" in out
        # Option sub-list filtered to the allowed subset only.
        assert "  - hard: ambiguous\n" in out
        assert "easy: explicit" not in out
        assert "medium: some inference" not in out

    def test_unsimplifiable_conjuncts_stacked(self, runner, tmp_path, monkeypatch):
        # NotEmpty (global) ∧ Matches (topic) does not simplify, so both render.
        structure = {"t": {"_meta": {"entry": {"query": {"type": "matches", "field": "query", "pattern": "[A-Z].*"}}}}}
        session = _session(tmp_path, monkeypatch, structure)
        out = _run(runner, session, "--topic", "t")
        assert "all of the following must hold: non-empty string; must match pattern '[A-Z].*'" in out
        assert "(narrowed for this topic)" in out

    def test_opaque_validator_marked_not_machine_checked(self, runner, tmp_path, monkeypatch):
        @register
        class _OpaquePrompt:
            tag = "opaque_prompt"

            def __init__(self, field):
                self.field = field

            def validate(self, value):
                return value

            def prompt(self):
                return f"{self.field}: looks fine"

            def to_json(self):
                return {"type": self.tag, "field": self.field}

            @classmethod
            def from_json(cls, d):
                return cls(d["field"])

        try:
            structure = {"t": {"_meta": {"entry": {"query": {"type": "opaque_prompt", "field": "query"}}}}}
            session = _session(tmp_path, monkeypatch, structure)
            out = _run(runner, session, "--topic", "t")
            assert "(not machine-checked)" in out
        finally:
            from okgv import validators

            validators.VALIDATOR_REGISTRY.pop("opaque_prompt", None)


class TestTopicConstraints:
    def test_function_and_argument_signature_rendered(self, runner, tmp_path, monkeypatch):
        structure = {
            "weather": {
                "current": {
                    "_meta": {
                        "function": "get_current_weather",
                        "required": {"location": {"type": "not_empty", "field": "location"}},
                        "optional": {"units": {"type": "one_of", "field": "units", "valid": ["celsius", "fahrenheit"]}},
                    }
                }
            }
        }
        session = _session(tmp_path, monkeypatch, structure)
        out = _run(runner, session, "--topic", "weather/current")
        assert "## Topic constraints — weather/current" in out
        assert "- **function**: must be `get_current_weather`" in out
        assert "    - `location` — non-empty string" in out
        assert "    - `units` — must be one of ['celsius', 'fahrenheit']" in out
        assert "- **similarity scope**: leaf" in out

    def test_forbidden_and_subtree_scope(self, runner, tmp_path, monkeypatch):
        parent_meta = {
            "function": "f",
            "similarity_scope": "subtree",
            "optional": {"u": {"type": "not_empty", "field": "u"}},
        }
        structure = {"p": {"_meta": parent_meta, "c": {"_meta": {"forbidden": ["u"]}}}}
        session = _session(tmp_path, monkeypatch, structure)
        out = _run(runner, session, "--topic", "p/c")
        assert "  - forbidden: `u`" in out
        assert "- **similarity scope**: subtree" in out

    def test_specless_topic_notes_global_only(self, runner, tmp_path, monkeypatch):
        session = _session(tmp_path, monkeypatch, {"t": {}})
        out = _run(runner, session, "--topic", "t")
        assert "No constraints are declared on this topic's path" in out
