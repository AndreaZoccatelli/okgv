"""Tests for okgv.specs: `_meta` parsing, path fold, and ingest-time analysis."""

import pytest

from okgv.errors import SpecError
from okgv.specs import (
    Spec,
    build_specs,
    collect_warnings,
    parse_meta,
    provably_disjoint,
    topic_paths,
)


def _one_of(field, *values):
    return {"type": "one_of", "field": field, "valid": list(values)}


def _not_empty(field):
    return {"type": "not_empty", "field": field}


# ── Backward compatibility: meta-less files ───────────────────────────────


class TestBackwardCompatible:
    def test_metaless_tree_has_empty_specs(self):
        structure = {"a": {"b": {}, "c": {}}, "d": {}}
        specs = build_specs(structure)
        assert set(specs) == {"a", "a/b", "a/c", "d"}
        assert all(s.is_empty() for s in specs.values())

    def test_topic_paths_ignores_metadata_keys(self):
        structure = {"a": {"_meta": {"function": "f"}, "b": {}}}
        assert topic_paths(structure) == {"a", "a/b"}


# ── Parsing one node's _meta block ────────────────────────────────────────


class TestParseMeta:
    def test_parses_function_and_validators(self):
        spec = parse_meta(
            {"function": "f", "required": {"x": _not_empty("x")}, "optional": {"y": _one_of("y", "a", "b")}},
            "t",
        )
        assert spec.function == "f"
        assert [v.__class__.__name__ for v in spec.required["x"]] == ["NotEmpty"]
        assert spec.optional["y"][0].valid == {"a", "b"}

    def test_validator_list_per_field(self):
        spec = parse_meta({"required": {"x": [_not_empty("x"), {"type": "matches", "field": "x", "pattern": "a+"}]}}, "t")
        assert [v.__class__.__name__ for v in spec.required["x"]] == ["NotEmpty", "Matches"]

    def test_unknown_meta_key_errors(self):
        with pytest.raises(SpecError, match="unknown _meta keys"):
            parse_meta({"requireds": {}}, "t")

    def test_bad_validator_tag_errors(self):
        with pytest.raises(SpecError, match="unknown validator type"):
            parse_meta({"required": {"x": {"type": "nope", "field": "x"}}}, "t")

    def test_bad_scope_errors(self):
        with pytest.raises(SpecError, match="similarity_scope must be one of"):
            parse_meta({"similarity_scope": "whole"}, "t")

    def test_forbidden_must_be_list_of_strings(self):
        with pytest.raises(SpecError, match="forbidden"):
            parse_meta({"forbidden": {"x": 1}}, "t")


# ── Fold: merge-class semantics ───────────────────────────────────────────


class TestFoldMergeClasses:
    def test_constraints_stack_across_dimensions(self):
        structure = {
            "p": {
                "_meta": {"required": {"a": _not_empty("a")}},
                "c": {"_meta": {"required": {"b": _not_empty("b")}}},
            }
        }
        leaf = build_specs(structure)["p/c"]
        assert set(leaf.required) == {"a", "b"}

    def test_child_narrows_parent_oneof(self):
        structure = {
            "p": {
                "_meta": {"optional": {"u": _one_of("u", "celsius", "fahrenheit")}},
                "c": {"_meta": {"required": {"u": _one_of("u", "celsius")}}},
            }
        }
        leaf = build_specs(structure)["p/c"]
        # optional promoted to required, OneOf narrowed to the intersection
        assert "u" in leaf.required and "u" not in leaf.optional
        assert leaf.required["u"][0].valid == {"celsius"}

    def test_policy_nearest_ancestor_wins(self):
        structure = {
            "p": {
                "_meta": {"similarity_scope": "subtree"},
                "c": {"_meta": {"similarity_scope": "leaf"}},
            }
        }
        specs = build_specs(structure)
        assert specs["p"].scope() == "subtree"
        assert specs["p/c"].scope() == "leaf"

    def test_identity_inherited_by_subtree(self):
        structure = {"p": {"_meta": {"function": "f"}, "c": {"d": {}}}}
        specs = build_specs(structure)
        assert specs["p/c"].function == "f"
        assert specs["p/c/d"].function == "f"

    def test_forbidden_drops_inherited_optional(self):
        structure = {
            "p": {
                "_meta": {"optional": {"u": _one_of("u", "x")}},
                "c": {"_meta": {"forbidden": ["u"]}},
            }
        }
        leaf = build_specs(structure)["p/c"]
        assert "u" not in leaf.optional and "u" in leaf.forbidden


# ── Fold: ingest errors ───────────────────────────────────────────────────


class TestFoldErrors:
    def test_contradiction_errors(self):
        structure = {
            "p": {
                "_meta": {"optional": {"u": _one_of("u", "x")}},
                "c": {"_meta": {"required": {"u": _one_of("u", "y")}}},
            }
        }
        with pytest.raises(SpecError, match="contradict"):
            build_specs(structure)

    def test_function_redeclaration_errors(self):
        structure = {"p": {"_meta": {"function": "f1"}, "c": {"_meta": {"function": "f2"}}}}
        with pytest.raises(SpecError, match="redeclaring"):
            build_specs(structure)

    def test_forbidden_conflicts_with_required(self):
        structure = {
            "p": {
                "_meta": {"required": {"u": _not_empty("u")}},
                "c": {"_meta": {"forbidden": ["u"]}},
            }
        }
        with pytest.raises(SpecError, match="forbidden but required"):
            build_specs(structure)


# ── Disjointness ──────────────────────────────────────────────────────────


class TestDisjointness:
    def test_required_vs_forbidden_is_disjoint(self):
        a = Spec(required={"u": []})
        b = Spec(forbidden={"u"})
        assert provably_disjoint(a, b)

    def test_oneof_empty_intersection_is_disjoint(self):
        from okgv.validators import OneOf

        a = Spec(required={"u": [OneOf("u", {"x"})]})
        b = Spec(required={"u": [OneOf("u", {"y"})]})
        assert provably_disjoint(a, b)

    def test_overlapping_not_disjoint(self):
        from okgv.validators import OneOf

        a = Spec(required={"u": [OneOf("u", {"x", "y"})]})
        b = Spec(required={"u": [OneOf("u", {"y", "z"})]})
        assert not provably_disjoint(a, b)


# ── Warnings ──────────────────────────────────────────────────────────────


class TestWarnings:
    def _messages(self, specs, level=None):
        return [w["message"] for w in collect_warnings(specs) if level is None or w["level"] == level]

    def test_global_schema_only_warning(self):
        specs = build_specs({"a": {}})
        msgs = self._messages(specs, "warning")
        assert any("global schema only" in m for m in msgs)

    def test_opaque_validator_warning(self):
        # Matches has narrow(), but a registered custom tag without narrow() is opaque.
        from okgv import validators

        class Opaque:
            tag = "opaque_test"
            field = "q"

            def __init__(self, field):
                self.field = field

            def validate(self, value):
                return value

            def prompt(self):
                return "q: anything"

            @classmethod
            def from_json(cls, d):
                return cls(d["field"])

        validators.VALIDATOR_REGISTRY["opaque_test"] = Opaque
        try:
            specs = build_specs({"a": {"_meta": {"required": {"q": {"type": "opaque_test", "field": "q"}}}}})
            msgs = self._messages(specs, "warning")
            assert any("contradiction and disjointness checks disabled" in m and "a.q" in m for m in msgs)
        finally:
            validators.VALIDATOR_REGISTRY.pop("opaque_test", None)

    def test_disjoint_siblings_noted_safe(self):
        structure = {
            "p": {
                "_meta": {"function": "f", "optional": {"u": _one_of("u", "x")}},
                "metric": {"_meta": {"required": {"u": _one_of("u", "x")}}},
                "none": {"_meta": {"forbidden": ["u"]}},
            }
        }
        infos = self._messages(build_specs(structure), "info")
        assert any("provably disjoint" in m for m in infos)

    def test_overlapping_siblings_warn_to_set_scope(self):
        structure = {
            "p": {
                "_meta": {"function": "f"},
                "a": {"_meta": {"optional": {"u": _one_of("u", "x", "y")}}},
                "b": {"_meta": {"optional": {"u": _one_of("u", "y", "z")}}},
            }
        }
        msgs = self._messages(build_specs(structure), "warning")
        assert any("not provably disjoint" in m and "similarity_scope" in m for m in msgs)

    def test_explicit_scope_suppresses_overlap_warning(self):
        structure = {
            "p": {
                "_meta": {"function": "f"},
                "a": {"_meta": {"similarity_scope": "subtree", "optional": {"u": _one_of("u", "x", "y")}}},
                "b": {"_meta": {"optional": {"u": _one_of("u", "y", "z")}}},
            }
        }
        msgs = self._messages(build_specs(structure), "warning")
        assert not any("not provably disjoint" in m for m in msgs)


# ── Session integration: in-memory spec mapping + drift check ─────────────


class TestSessionSpecs:
    def _session(self, tmp_path, structure, monkeypatch, make_db=True):
        import json

        from okgv.session import Session
        from tests.unit.conftest import MockGraphDB

        sfile = tmp_path / "structure.json"
        sfile.write_text(json.dumps(structure))
        monkeypatch.setenv("OKGV_STRUCTURE", str(sfile))
        db_path = tmp_path / "okgv.db"
        if make_db:
            db_path.touch()
        return Session(graph_db=MockGraphDB(), db_path=db_path)

    def test_specs_loaded_from_structure_file(self, tmp_path, monkeypatch):
        s = self._session(tmp_path, {"w": {"_meta": {"function": "f"}, "c": {}}}, monkeypatch)
        assert s.effective_spec("w/c").function == "f"

    def test_specs_empty_without_structure_file(self, tmp_path, monkeypatch):
        from okgv.session import Session
        from tests.unit.conftest import MockGraphDB

        monkeypatch.setenv("OKGV_STRUCTURE", str(tmp_path / "missing.json"))
        s = Session(graph_db=MockGraphDB(), db_path=tmp_path / "okgv.db")
        assert s.specs == {}

    def test_consistency_warns_on_drift(self, tmp_path, monkeypatch):
        s = self._session(tmp_path, {"a": {"b": {}}}, monkeypatch)
        s.graph_db.create_topic("a")  # missing a/b; has nothing extra
        warnings = s.check_structure_consistency()
        assert any("a/b" in w and "not in the DB" in w for w in warnings)

    def test_consistency_silent_when_matched(self, tmp_path, monkeypatch):
        s = self._session(tmp_path, {"a": {"b": {}}}, monkeypatch)
        s.graph_db.create_topic("a")
        s.graph_db.create_subtopic("a", "b")
        assert s.check_structure_consistency() == []

    def test_consistency_skipped_without_db(self, tmp_path, monkeypatch):
        s = self._session(tmp_path, {"a": {}}, monkeypatch, make_db=False)
        assert s.check_structure_consistency() == []
