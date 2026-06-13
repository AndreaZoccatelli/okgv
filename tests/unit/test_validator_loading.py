"""Tests for OKGV_VALIDATORS: registering custom validators before a fold."""

import json
import sys

import pytest
from click.testing import CliRunner

from okgv import validators
from okgv.config import load_validators
from okgv.main import cli
from okgv.session import Session
from tests.unit.conftest import MockGraphDB, MockVectorDB, fake_embedder

_MODULE_NAME = "okgv_even_validator"
_MODULE_SOURCE = """
from okgv.validators import register


@register
class Even:
    tag = "even_test"
    args = ()

    def __init__(self, field):
        self.field = field

    def validate(self, value):
        if value % 2 != 0:
            raise ValueError(f"{self.field}: must be even, got {value}")
        return value

    def prompt(self):
        return f"{self.field}: even integer"

    def to_json(self):
        return {"type": self.tag, "field": self.field}

    @classmethod
    def from_json(cls, d):
        return cls(d["field"])
"""


@pytest.fixture
def custom_validator_module(tmp_path, monkeypatch):
    """Write a registrable validator module, importable by name, and clean up
    the global registry / sys.modules afterward."""
    (tmp_path / f"{_MODULE_NAME}.py").write_text(_MODULE_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        yield _MODULE_NAME
    finally:
        validators.VALIDATOR_REGISTRY.pop("even_test", None)
        sys.modules.pop(_MODULE_NAME, None)


@pytest.fixture
def runner():
    return CliRunner()


class TestLoadValidators:
    def test_imports_and_registers(self, custom_validator_module, monkeypatch):
        assert "even_test" not in validators.VALIDATOR_REGISTRY
        monkeypatch.setenv("OKGV_VALIDATORS", custom_validator_module)
        imported = load_validators()
        assert imported == [custom_validator_module]
        assert "even_test" in validators.VALIDATOR_REGISTRY

    def test_no_env_is_noop(self, monkeypatch):
        monkeypatch.delenv("OKGV_VALIDATORS", raising=False)
        assert load_validators() == []

    def test_missing_module_errors(self, monkeypatch):
        monkeypatch.setenv("OKGV_VALIDATORS", "definitely_not_a_module_xyz")
        with pytest.raises(ImportError, match="OKGV_VALIDATORS"):
            load_validators()


class TestCreateStructureWithCustomValidator:
    def _session(self, tmp_path):
        return Session(
            graph_db=MockGraphDB(),
            vector_db=MockVectorDB(),
            embedder=fake_embedder,
            db_path=tmp_path / "okgv.db",
        )

    def _structure(self):
        # bare-tag form of the custom validator on an entry field
        return json.dumps({"a": {"_meta": {"entry": {"score": "even_test"}}}})

    def test_custom_tag_unknown_without_env(self, runner, tmp_path, monkeypatch):
        monkeypatch.delenv("OKGV_VALIDATORS", raising=False)
        session = self._session(tmp_path)
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=session, input=self._structure())
        assert result.exit_code == 2
        assert "even_test" in result.stderr and "unknown validator" in result.stderr

    def test_custom_tag_resolves_with_env(self, runner, tmp_path, monkeypatch, custom_validator_module):
        monkeypatch.setenv("OKGV_VALIDATORS", custom_validator_module)
        session = self._session(tmp_path)
        result = runner.invoke(cli, ["create-structure", "--file", "-"], obj=session, input=self._structure())
        assert result.exit_code == 0
        assert "a" in session.graph_db.topics
