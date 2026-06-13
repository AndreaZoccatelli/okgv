"""Effective-spec fold over the topic tree.

A ``structure.json`` node may carry a ``_meta`` block describing constraints on
the entries placed under it. ``_meta`` blocks compose along a root-to-leaf path:
the effective spec for a topic is the fold of every ancestor's ``_meta`` plus
its own. A file with no ``_meta`` keys parses exactly as before (every effective
spec is empty), so the feature is opt-in and backward compatible.

Three merge classes (fix.md section 5):

  - **constraints** (``required``, ``optional``, ``forbidden``, ``entry``):
    combined by conjunction. A child may narrow an existing field (a tighter
    ``OneOf``/``InRange``), add a new field, promote an optional key to
    required, or forbid an inherited optional key. A contradiction (``narrow``
    proves the conjunction unsatisfiable, or a required key is forbidden) is an
    ingest error.
  - **policy** (``similarity_scope``): nearest ancestor wins.
  - **identity** (``function``): set once on the path and inherited by the whole
    subtree. Redeclaration anywhere below is an error, never an override.

Parsing routes every validator dict through the okgv validator registry
(``validator_from_json``), so a misspelled tag dies at ingest with a precise
error rather than silently at validation time.

``narrow`` is partial: cross-type pairs and custom validators without a
``narrow()`` method are opaque. Enforcement never depends on it (the example
schema runs every conjunct), but analysis does, so opaque validators disable
contradiction and disjointness checks for the field they sit on. Callers surface
that as a warning rather than degrading silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from okgv.errors import SpecError
from okgv.validators import NEVER, narrow, validator_from_json

# Keys recognized inside a `_meta` block. Constraint targets plus the policy and
# identity keys. Anything else is a typo and fails at ingest.
_PARAM_TARGETS = ("required", "optional", "entry")
_META_KEYS = {*_PARAM_TARGETS, "forbidden", "function", "similarity_scope"}
_SCOPES = ("leaf", "subtree")


@dataclass
class Spec:
    """An effective (folded) constraint spec for one topic path.

    ``required``/``optional``/``entry`` map a name to a *list* of validators:
    conjunction may leave validators stacked when ``narrow`` cannot simplify
    them, and validation runs every one. ``required``/``optional`` target
    argument parameters; ``entry`` targets entry-schema fields.
    """

    function: str | None = None
    required: dict[str, list] = field(default_factory=dict)
    optional: dict[str, list] = field(default_factory=dict)
    forbidden: set[str] = field(default_factory=set)
    entry: dict[str, list] = field(default_factory=dict)
    similarity_scope: str | None = None

    def is_empty(self) -> bool:
        """True when nothing on the path declared any `_meta`."""
        return (
            self.function is None
            and not self.required
            and not self.optional
            and not self.forbidden
            and not self.entry
            and self.similarity_scope is None
        )

    def scope(self) -> str:
        """Resolved similarity scope, defaulting to ``leaf``."""
        return self.similarity_scope or "leaf"

    def opaque_fields(self) -> list[str]:
        """Names carrying a validator with no ``narrow()`` (analysis-blind)."""
        blind = []
        for store in (self.required, self.optional, self.entry):
            for name, validators in store.items():
                if any(not hasattr(v, "narrow") for v in validators):
                    blind.append(name)
        return sorted(set(blind))


# ── Parsing one node's _meta block ────────────────────────────────────────


def _parse_validators(value, topic: str, target: str, name: str) -> list:
    """Parse a single validator dict or a list of them into validator objects."""
    raw = value if isinstance(value, list) else [value]
    parsed = []
    for item in raw:
        if not isinstance(item, dict):
            raise SpecError(
                f"topic '{topic}': {target}.{name} must be a validator object or a list of them, "
                f"got {type(item).__name__}"
            )
        try:
            parsed.append(validator_from_json(item))
        except ValueError as e:
            raise SpecError(f"topic '{topic}': {target}.{name}: {e}") from e
    return parsed


def parse_meta(meta, topic: str) -> Spec:
    """Parse one node's raw ``_meta`` dict into an unfolded :class:`Spec`."""
    if not isinstance(meta, dict):
        raise SpecError(f"topic '{topic}': _meta must be a JSON object, got {type(meta).__name__}")

    unknown = set(meta) - _META_KEYS
    if unknown:
        raise SpecError(f"topic '{topic}': unknown _meta keys {sorted(unknown)}, known: {sorted(_META_KEYS)}")

    spec = Spec()

    if "function" in meta:
        fn = meta["function"]
        if not isinstance(fn, str):
            raise SpecError(f"topic '{topic}': function must be a string, got {type(fn).__name__}")
        spec.function = fn

    for target in _PARAM_TARGETS:
        block = meta.get(target)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise SpecError(f"topic '{topic}': {target} must be a JSON object, got {type(block).__name__}")
        store = getattr(spec, target)
        for name, value in block.items():
            store[name] = _parse_validators(value, topic, target, name)

    if "forbidden" in meta:
        fb = meta["forbidden"]
        if not isinstance(fb, list) or not all(isinstance(k, str) for k in fb):
            raise SpecError(f"topic '{topic}': forbidden must be a list of key names")
        spec.forbidden = set(fb)

    if "similarity_scope" in meta:
        sc = meta["similarity_scope"]
        if sc not in _SCOPES:
            raise SpecError(f"topic '{topic}': similarity_scope must be one of {list(_SCOPES)}, got '{sc}'")
        spec.similarity_scope = sc

    return spec


# ── Folding a root-to-leaf chain of specs ─────────────────────────────────


def _conjoin(existing: list, additions: list, topic: str, name: str) -> list:
    """Conjunction of two validator lists on the same name.

    Simplifies with ``narrow`` where possible, stacks where it cannot, and
    raises :class:`SpecError` when a pair is provably unsatisfiable (``NEVER``).
    Validators whose ``field`` differs are never narrowed against each other
    (``narrow`` forbids that); they simply stack and both run.
    """
    stack = list(existing)
    for nv in additions:
        merged = nv
        kept = []
        for ev in stack:
            if getattr(ev, "field", None) != getattr(merged, "field", object()):
                kept.append(ev)
                continue
            result = narrow(ev, merged)
            if result is NEVER:
                raise SpecError(
                    f"topic '{topic}': constraints on '{name}' contradict "
                    f"({ev.__class__.__name__} and {merged.__class__.__name__} share no valid value)"
                )
            if result is None:
                kept.append(ev)
            else:
                merged = result
        kept.append(merged)
        stack = kept
    return stack


def _add_param(eff: Spec, name: str, validators: list, required: bool, topic: str) -> None:
    """Merge an argument-parameter constraint into the effective spec.

    ``required`` and ``optional`` share a namespace: once required (here or by an
    ancestor), a key stays required (a child cannot relax it back to optional).
    """
    prior = eff.required.pop(name, None)
    was_required = prior is not None
    if prior is None:
        prior = eff.optional.pop(name, None) or []
    merged = _conjoin(prior, validators, topic, name)
    if required or was_required:
        eff.required[name] = merged
    else:
        eff.optional[name] = merged


def fold(node_specs: list[Spec], topic: str) -> Spec:
    """Fold a root-to-leaf list of node specs into one effective spec."""
    eff = Spec()
    for spec in node_specs:
        if spec.function is not None:
            if eff.function is not None:
                raise SpecError(
                    f"topic '{topic}': function already set to '{eff.function}' by an ancestor; "
                    f"redeclaring it ('{spec.function}') is not allowed (identity is set once on the path)"
                )
            eff.function = spec.function

        if spec.similarity_scope is not None:
            eff.similarity_scope = spec.similarity_scope  # nearest ancestor wins (deepest seen)

        for name, validators in spec.required.items():
            _add_param(eff, name, validators, required=True, topic=topic)
        for name, validators in spec.optional.items():
            _add_param(eff, name, validators, required=False, topic=topic)
        for name, validators in spec.entry.items():
            eff.entry[name] = _conjoin(eff.entry.get(name, []), validators, topic, name)

        for key in spec.forbidden:
            if key in eff.required:
                raise SpecError(
                    f"topic '{topic}': key '{key}' is forbidden but required by an ancestor (or this node)"
                )
            eff.optional.pop(key, None)  # forbidding narrows away an inherited optional key
            eff.forbidden.add(key)

    return eff


# ── Building every effective spec from a structure dict ───────────────────


def _children(node) -> dict:
    """Topic children of a structure node (skips `_`-prefixed metadata keys)."""
    if not isinstance(node, dict):
        return {}
    return {k: v for k, v in node.items() if not k.startswith("_")}


def topic_paths(structure: dict) -> set[str]:
    """Every topic path declared in a structure dict (metadata keys excluded)."""
    paths: set[str] = set()

    def walk(node: dict, prefix: str | None) -> None:
        for name, value in _children(node).items():
            path = f"{prefix}/{name}" if prefix else name
            paths.add(path)
            walk(value if isinstance(value, dict) else {}, path)

    walk(structure, None)
    return paths


def build_specs(structure: dict) -> dict[str, Spec]:
    """Effective spec for every topic path in a structure dict.

    Parses and folds every ``_meta`` block; a contradiction, redeclaration, or
    malformed validator raises :class:`SpecError` (an ingest error). Topics with
    no ``_meta`` anywhere on their path get an empty :class:`Spec`.
    """
    specs: dict[str, Spec] = {}

    def walk(node: dict, prefix: str | None, chain: list[Spec]) -> None:
        for name, value in _children(node).items():
            path = f"{prefix}/{name}" if prefix else name
            meta = value.get("_meta") if isinstance(value, dict) else None
            node_spec = parse_meta(meta, path) if meta is not None else Spec()
            sub_chain = chain + [node_spec]
            specs[path] = fold(sub_chain, path)
            walk(value if isinstance(value, dict) else {}, path, sub_chain)

    walk(structure, None, [])
    return specs


# ── Sibling disjointness (dedup-scope analysis) ───────────────────────────


def _parent(path: str) -> str | None:
    return path.rsplit("/", 1)[0] if "/" in path else None


def collect_warnings(specs: dict[str, Spec]) -> list[dict]:
    """Ingest-time advisories over the folded specs.

    Returns ``{level, message}`` dicts (``info`` or ``warning``):

      - a topic whose path carries no ``_meta`` at all (global schema only);
      - a field whose validator is opaque to analysis (no ``narrow()``);
      - sibling pairs: provably disjoint ones are noted leaf-scope safe, and
        pairs that cannot be proven disjoint warn to set ``similarity_scope``
        explicitly when neither already did.
    """
    out: list[dict] = []

    for path in sorted(specs):
        spec = specs[path]
        if spec.is_empty():
            out.append(
                {
                    "level": "warning",
                    "message": f"topic '{path}' has no _meta on its path; "
                    "entries validate against the global schema only",
                }
            )
        for name in spec.opaque_fields():
            out.append(
                {
                    "level": "warning",
                    "message": f"custom validator on '{path}.{name}': contradiction and disjointness "
                    "checks disabled for this field, verify filtering rules manually",
                }
            )

    siblings: dict[str | None, list[str]] = {}
    for path in specs:
        siblings.setdefault(_parent(path), []).append(path)

    for group in siblings.values():
        group.sort()
        for i, a_path in enumerate(group):
            for b_path in group[i + 1 :]:
                a, b = specs[a_path], specs[b_path]
                if provably_disjoint(a, b):
                    out.append(
                        {
                            "level": "info",
                            "message": f"siblings '{a_path}' and '{b_path}' are provably disjoint; "
                            "leaf similarity scope is safe",
                        }
                    )
                elif a.similarity_scope is None and b.similarity_scope is None:
                    out.append(
                        {
                            "level": "warning",
                            "message": f"siblings '{a_path}' and '{b_path}' are not provably disjoint; "
                            "set similarity_scope explicitly to choose leaf vs subtree dedup",
                        }
                    )

    return out


def _param_validators(spec: Spec) -> dict[str, list]:
    merged: dict[str, list] = {}
    for store in (spec.required, spec.optional):
        for name, validators in store.items():
            merged.setdefault(name, []).extend(validators)
    return merged


def provably_disjoint(a: Spec, b: Spec) -> bool:
    """True when no entry can validate under both specs (closed vocabulary only).

    Proven by a required key forbidden in the sibling, or a shared field whose
    validators ``narrow`` to ``NEVER``. "Cannot prove" returns False
    (conservatively treated as overlapping); opaque validators simply never
    contribute a proof.
    """
    if set(a.required) & b.forbidden or set(b.required) & a.forbidden:
        return True
    for amap, bmap in ((_param_validators(a), _param_validators(b)), (a.entry, b.entry)):
        for name in set(amap) & set(bmap):
            for va in amap[name]:
                for vb in bmap[name]:
                    if getattr(va, "field", None) != getattr(vb, "field", object()):
                        continue
                    if narrow(va, vb) is NEVER:
                        return True
    return False
