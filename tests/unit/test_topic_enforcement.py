"""Tests for E5 (spec threaded to the hook) and E6 (library default enforcement
of the `entry` namespace)."""

import json

import pytest
from click.testing import CliRunner

from okgv.core import _hook_accepts_spec, enforce_entry_spec, validate_entry_topic
from okgv.errors import EntryError
from okgv.main import cli
from okgv.protocols import PropertyDefinition
from okgv.session import Session
from okgv.specs import parse_meta
from tests.unit.conftest import MockGraphDB, MockVectorDB, fake_embedder


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Bare:
    """A schema with no validate_for_topic hook."""


# ── E6: enforce_entry_spec ────────────────────────────────────────────────


class TestEnforceEntrySpec:
    def test_rejects_violation(self):
        spec = parse_meta({"entry": {"difficulty": ["hard"]}}, "t")
        with pytest.raises(ValueError, match="difficulty"):
            enforce_entry_spec(spec, _Obj(difficulty="easy"))
        enforce_entry_spec(spec, _Obj(difficulty="hard"))  # valid: no raise

    def test_missing_field_errors(self):
        spec = parse_meta({"entry": {"difficulty": ["hard"]}}, "t")
        with pytest.raises(ValueError, match="not present on the entry"):
            enforce_entry_spec(spec, _Obj(other=1))

    def test_only_entry_namespace_enforced(self):
        # required/optional (argument-object namespaces) are NOT auto-enforced
        spec = parse_meta({"required": {"location": "not_empty"}}, "t")
        enforce_entry_spec(spec, _Obj())  # no 'location' attr, but not enforced → no raise


# ── E5/E6: validate_entry_topic ───────────────────────────────────────────


class TestValidateEntryTopic:
    def test_default_enforces_entry_without_hook(self):
        spec = parse_meta({"entry": {"difficulty": ["hard"]}}, "t")
        with pytest.raises(EntryError, match="rejected for topic"):
            validate_entry_topic(_Bare(), _Obj(difficulty="easy"), "t", spec)
        validate_entry_topic(_Bare(), _Obj(difficulty="hard"), "t", spec)

    def test_no_spec_no_hook_is_noop(self):
        validate_entry_topic(_Bare(), _Obj(), "t", None)  # nothing to do

    def test_three_arg_hook_receives_spec(self):
        seen = {}

        class S:
            @staticmethod
            def validate_for_topic(entry, topic, spec):
                seen["spec"] = spec

        spec = parse_meta({"function": "f"}, "t")
        validate_entry_topic(S(), _Obj(), "t", spec)
        assert seen["spec"] is spec

    def test_two_arg_hook_still_called(self):
        seen = {}

        class S:
            @staticmethod
            def validate_for_topic(entry, topic):
                seen["topic"] = topic

        validate_entry_topic(S(), _Obj(), "top", parse_meta({"function": "f"}, "top"))
        assert seen["topic"] == "top"

    def test_default_runs_before_hook(self):
        # entry-namespace violation should fire even if the hook would pass
        order = []

        class S:
            @staticmethod
            def validate_for_topic(entry, topic, spec):
                order.append("hook")

        spec = parse_meta({"entry": {"difficulty": ["hard"]}}, "t")
        with pytest.raises(EntryError):
            validate_entry_topic(S(), _Obj(difficulty="easy"), "t", spec)
        assert order == []  # hook never reached


class TestHookAcceptsSpec:
    def test_arity(self):
        assert _hook_accepts_spec(lambda e, t, s: None) is True
        assert _hook_accepts_spec(lambda e, t: None) is False
        assert _hook_accepts_spec(lambda *a: None) is True
        assert _hook_accepts_spec(lambda e, t, s=None: None) is True


# ── E6 end-to-end: a schema with no hook still gets entry narrowing ───────


class _Entry:
    def __init__(self, raw: dict):
        self.text = raw["text"]
        self.difficulty = raw["difficulty"]


class _NoHookSchema:
    """No validate_for_topic — relies entirely on the library default."""

    entry_class = _Entry

    @staticmethod
    def metadata(entry):
        return {"difficulty": entry.difficulty}

    @staticmethod
    def graph_properties(entry):
        return {"text": entry.text}

    @staticmethod
    def vector_properties(entry):
        return {"text": entry.text}

    @staticmethod
    def embedding_text(entry):
        return entry.text

    @staticmethod
    def vector_property_definitions():
        return [PropertyDefinition("difficulty", "text"), PropertyDefinition("text", "text")]


class TestDefaultEnforcementE2E:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _session(self, tmp_path, monkeypatch):
        structure = {"t": {"_meta": {"entry": {"difficulty": ["hard"]}}}}
        sfile = tmp_path / "structure.json"
        sfile.write_text(json.dumps(structure))
        monkeypatch.setenv("OKGV_STRUCTURE", str(sfile))
        return Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            schema=_NoHookSchema(),
            db_path=tmp_path / "okgv.db",
        )

    def test_violating_entry_rejected_without_hook(self, runner, tmp_path, monkeypatch):
        session = self._session(tmp_path, monkeypatch)
        result = runner.invoke(
            cli, ["submit", "--topic", "t", "--entry", json.dumps({"text": "x", "difficulty": "easy"})], obj=session
        )
        assert result.exit_code == 2
        assert "missing_field" in result.stderr or "rejected for topic" in result.stderr

    def test_conforming_entry_accepted(self, runner, tmp_path, monkeypatch):
        session = self._session(tmp_path, monkeypatch)
        result = runner.invoke(
            cli, ["submit", "--topic", "t", "--entry", json.dumps({"text": "x", "difficulty": "hard"})], obj=session
        )
        assert result.exit_code == 0
