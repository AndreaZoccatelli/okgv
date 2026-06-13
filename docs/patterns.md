<p align="center">
  <img src="../resources/okgv-logo-light.svg#gh-light-mode-only" alt="okgv logo" width="400">
  <img src="../resources/okgv-logo-dark.svg#gh-dark-mode-only" alt="okgv logo" width="400">
</p>

# Dataset Patterns

This document is a catalog of synthetic-dataset shapes and how each one maps onto okgv's three configuration surfaces. It assumes you have read [Entry Schema & Configuration](schema.md) and [Architecture & Internals](architecture.md); it does not re-explain the API, it shows how to *compose* it. The goal is to make the design space concrete: given a dataset you want to build, which knobs do you turn, and which do you leave alone.

For the reasoning *behind* these recipes, how deep to make the tree, when a dimension belongs in the structure versus a field, how to size a leaf, see [Design Principles](principles.md). This document is the applied counterpart: principles in, recipes out.

## The three surfaces

Every okgv project is configured through three surfaces, and almost every design decision is a choice about which surface carries a given rule.

| Surface | File | Carries |
|---------|------|---------|
| **Topic tree** | `config/structure.json` | The partition. It is simultaneously the *balance stratum* (recursive counts) and the *dedup scope* (similarity is scoped per topic). |
| **Entry schema** | `config/schema.py` | The global shape every entry meets, regardless of topic: fields, global validators, store mapping (`metadata`/`graph`/`vector`), embedding text, balance fields. |
| **Per-topic constraints** | `_meta` in `structure.json` | Rules that differ *by position in the tree*. Folded root-to-leaf; a child may only narrow, add, or forbid, never relax. |

The load-bearing idea: **a rule that is true for every entry belongs in the schema; a rule that depends on where the entry sits belongs in `_meta`; a rule that is really about partitioning or balancing belongs in the tree shape itself.** Most design mistakes are a rule placed on the wrong surface.

Two derived choices recur across every pattern and are called out per use case:

- **Does this dataset need `_meta` at all?** (Section per case + consolidated in [When to use `_meta`](#when-to-use-_meta-and-when-not).)
- **`leaf` or `subtree` similarity scope?** (Consolidated in [Similarity scope](#choosing-similarity-scope).)

---

## Use case 1: Classification / labeled text

**Shape.** Short texts each carrying one or more categorical labels (sentiment, intent, topic label, toxicity). The label set is closed. You want the dataset balanced across labels and free of near-duplicates *within a label region*.

**Tree.** The label that defines the semantic region is the tree. If intent classification has domains, nest them; otherwise the tree is one level.

```json
{
  "billing": { "refund": {}, "charge_dispute": {}, "invoice_request": {} },
  "technical": { "login_issue": {}, "bug_report": {}, "feature_request": {} },
  "account": { "password_reset": {}, "close_account": {} }
}
```

`{}` is a leaf. Entries attach to leaves only. Here the leaf *is* the intent label, which is the cleanest arrangement: similarity is automatically scoped to "other utterances with this same intent", which is exactly the dedup boundary you want.

**Schema.** The label is a global `OneOf`; the text is `NotEmpty`. Balance across the label that is *not* encoded by the tree, if `intent` is the leaf path, balance on a secondary axis like `difficulty` or `channel`, otherwise there is nothing to balance (the tree already balances intent via `least-topic`).

```python
from okgv.validators import OneOf, NotEmpty

text = NotEmpty("text")
channel = OneOf("channel", {"chat", "email", "voice"})

class UtteranceSchema:
    entry_class = UtteranceEntry
    validators = [text, channel]
    balance_fields = ["channel"]   # intent is balanced by the tree, channel by the field
```

**Validation.** Global validators are sufficient. There is no relational rule between a field and its topic beyond "this leaf means this label", and that is already enforced structurally: the entry lands under `billing/refund`, so its intent *is* refund by construction.

**`_meta` verdict: do not use it.** When the leaf path encodes the label, `_meta` would only restate what the tree already says. The only reason to reach for it is if a *secondary scalar field* needs narrowing under some leaves (e.g. `channel` restricted to `voice` under a phone-only subtree), that is the `entry` namespace, auto-enforced. Absent that, keep it schema-only. (Note `forbidden` does **not** apply here: it removes keys from a compound *argument object*, not top-level entry fields, so it has no role in plain classification.)

**Scope: `leaf`** (the default). Each intent is its own semantic island; you do not want a `refund` utterance deduped against a `bug_report`.

---

## Use case 2: Instruction / Q&A tuning with graded difficulty

**Shape.** Question-answer (or instruction-response) pairs over a subject hierarchy, each graded `easy`/`medium`/`hard`, balanced across both subject and difficulty. This is the archetype the README opens with (algebra/calculus questions × easy/medium/hard).

**Tree.** Subjects nest naturally; make the leaves fine enough that per-leaf entry counts stay bounded (the README's "few thousand per leaf" guidance, dedup cost tracks leaf size).

```json
{
  "algebra": {
    "linear_algebra": { "basics": {}, "eigenvalues": {} },
    "abstract_algebra": { "groups": {}, "rings": {} }
  },
  "calculus": {
    "differential": { "limits": {}, "derivatives": {} },
    "integral": { "techniques": {}, "applications": {} }
  }
}
```

**Schema.** `question`/`answer` are `NotEmpty`; `difficulty` is a global `OneOf`. Put `difficulty` in `metadata()` so it is stored in both DBs and usable as a balance field. Embed the question (that is the dedup axis, you do not want two phrasings of the same question, regardless of answer).

```python
difficulty = OneOf("difficulty", {"easy", "medium", "hard"})

class QASchema:
    entry_class = QAEntry
    validators = [question, answer, difficulty]
    balance_fields = ["difficulty"]
    field_descriptions = {
        "difficulty": ("cognitive load for a graduate student", {
            "easy": "single concept, direct application",
            "medium": "combine 2-3 concepts",
            "hard": "multi-step reasoning, edge cases",
        }),
    }

    @staticmethod
    def metadata(entry):              # both stores → balanceable + reportable
        return {"difficulty": entry.difficulty}
    @staticmethod
    def embedding_text(entry):        # dedup on the question
        return entry.question
```

The agent runs `least-topic` to find the underrepresented `subject × difficulty` cell, generates there, checks `similar` against the leaf, and submits. The tree handles subject balance; `balance_fields=["difficulty"]` makes `topic-stats` report the difficulty split per leaf, while `report` gives the dataset-level balance view across all leaves, both default to the balance fields, so the agent (or you) can see skew without naming fields.

**Validation.** Global validators cover it *unless* you want difficulty to be restricted per topic, e.g. a `basics` leaf may only hold `easy`/`medium`, never `hard`. That is the first real use of `_meta`:

```json
{
  "algebra": {
    "linear_algebra": {
      "basics":      { "_meta": { "entry": { "difficulty": { "one_of": ["easy", "medium"] } } } },
      "eigenvalues": { "_meta": { "entry": { "difficulty": { "one_of": ["medium", "hard"] } } } }
    }
  }
}
```

**`_meta` verdict: use the `entry` namespace, no hook.** `difficulty` is a scalar entry attribute, so okgv enforces this for you on every submit/move/`revalidate`, a `hard` entry submitted to `basics` is rejected automatically. This is the "default enforcement, no code" path: the `entry` namespace narrows a global field per topic and the library runs it. You write zero Python for this. (Requirement: `difficulty` must be a stored attribute or `@property` on the entry, not a value computed only inside `metadata()`.)

If you do *not* need per-topic difficulty narrowing, skip `_meta` entirely, the global `OneOf` already guarantees a valid difficulty everywhere.

**Scope: `leaf`**, provided leaves are semantically disjoint (a derivatives question and a limits question should not dedup against each other). If two leaves genuinely overlap, see [the subtree case](#use-case-4--retrieval--rag-evaluation-pairs).

---

## Use case 3: Function-calling / tool-use data (the deep `_meta` case)

**Shape.** A user query paired with the correct function call: a function name plus an arguments object. Each function has a *signature*, required params, optional params, value constraints, and those differ per function. This is the worked `example/` project; this section explains *why* it is shaped that way.

**Tree.** Group functions by domain; the leaf is one function.

```json
{
  "weather": {
    "current_conditions": { "_meta": { "function": "get_current_weather",
        "required": {"location": "not_empty"},
        "optional": {"units": {"one_of": ["celsius", "fahrenheit"]}} } },
    "forecast": { "_meta": { "function": "get_forecast",
        "required": {"location": "not_empty", "days": {"is_type": ["int"]}} } }
  }
}
```

**Why `_meta` is essential here.** The rule "an entry under `forecast` must call `get_forecast` with a `location` and an integer `days`" is *positional*, it is different for every leaf, and it is *relational*: it constrains the relationship between the `function` field and the `arguments` object. Neither a global validator (the signature differs per topic) nor the `entry` namespace alone (the arguments object is compound, not a scalar) can express it. This is the boundary where `_meta` plus a hook earns its keep.

**The two `_meta` namespaces in play:**

- `function`, identity, set once on a path and inherited by the subtree. Redeclaring it below is an ingest error. It is what guarantees every entry in the subtree is *about* that function.
- `required`/`optional`/`forbidden`, the *argument signature*. These describe a compound field (the arguments dict), so okgv does **not** auto-enforce them. You bind them to your entry in `validate_for_topic`, fed the folded `Spec`. The example's `FunctionSpec.from_effective(spec)` is exactly this combinator: it checks the function name matches, every required key is present, no forbidden key appears, no unknown key sneaks in, and each argument passes its validators.

**Folding does real work.** A parent can declare a shared `required` and children narrow it. The `current_conditions/metric` leaf in the example narrows `units` to `{"one_of": ["celsius"]}` over the parent's `["celsius","fahrenheit"]`; `no_unit_stated` *forbids* `units` entirely. You write the common signature once at the parent and specialize per leaf, and a contradictory specialization (narrowing `units` to a value the parent excludes) fails at `create-structure`, before any data is generated.

**Validation.** Global validators guarantee the entry *parses* (`function` is a known name, `arguments` is a dict, `query` is non-empty). The hook guarantees it is *correct for its topic*. Note the example deliberately makes a topic with no `function` on its path a hard error in the hook, a query-to-function pair with no declared function is unverifiable training data, so it is refused at the last cheap gate rather than stored.

**`_meta` verdict: use it fully, `function` + argument namespaces + a `validate_for_topic` hook.** This is the pattern that uses every facet. If your "tool-use" data is actually just classified text with no argument structure, you are in Use case 1, not here.

**Scope: `leaf`.** Each function is its own dedup island. Two queries that both map to `get_forecast` should dedup; a forecast query and an alert query should not.

---

## Use case 4: Retrieval / RAG evaluation pairs

**Shape.** `(query, relevant_passage)` pairs over a document taxonomy, used to evaluate a retriever. The catch: sibling topics often *overlap* semantically (a query about `vpn/setup` and one about `network/troubleshooting` can be near-duplicates), and you want dedup to catch that.

**Tree.** Mirror the document taxonomy. The overlap risk is between siblings under a shared parent.

```json
{
  "networking": {
    "_meta": { "similarity_scope": "subtree" },
    "vpn":     { "setup": {}, "troubleshooting": {} },
    "wifi":    { "setup": {}, "troubleshooting": {} },
    "general": {}
  }
}
```

**Why `subtree` here.** Default `leaf` scope compares a candidate only against entries in the exact target leaf. That is wrong when `vpn/troubleshooting` and `wifi/troubleshooting` can produce the same query. Setting `similarity_scope: subtree` on `networking` makes `similar` prefix-match the whole `networking/*` subtree; cross-leaf matches come back tagged `sibling: true`, so the agent treats them as variants to steer away from rather than automatic rejections. `create-structure` will *warn* you when siblings are not provably disjoint and no scope is set, that warning is the signal to make this choice consciously.

**Schema.** `query` and `passage` are `NotEmpty`; embed the `query`. There is usually no per-leaf field constraint, so the schema is thin.

**Validation.** Global validators only. The interesting decision in this pattern is the *scope policy*, which lives in `_meta` but uses none of its constraint machinery.

**`_meta` verdict: use `similarity_scope` only.** This is the one pattern where `_meta` carries a *policy* (nearest-ancestor-wins) rather than a constraint. No `function`, no `required`, no hook. It demonstrates that `_meta` is not all-or-nothing: you can reach for exactly the one facet you need.

---

## Use case 5: Paraphrase / diversity sets (the `_meta`-free, near-the-edge case)

**Shape.** A flat pool of diverse paraphrases or stylistic variants of seed sentences, no meaningful hierarchy, balance irrelevant. You want okgv purely for in-the-loop dedup feedback.

**Tree.** One node, or a shallow one keyed by seed:

```json
{ "seed_001": {}, "seed_002": {}, "seed_003": {} }
```

**Schema.** Minimal: `text` is `NotEmpty`, embed it. No balance fields, no descriptions beyond the field.

**Validation.** Global only.

**`_meta` verdict: none.** There is no positional rule and nothing to narrow. Adding `_meta` here would be ceremony.

**Honest caveat.** This sits at the edge of okgv's niche (see the README's "reach for something else"). With the tree collapsed, the balance machinery does nothing and you are using okgv as a dedup wrapper. That is *fine* if you want the agent-in-the-loop "nearest entry with full content" feedback rather than a cosine cutoff, but if a deterministic threshold over a fixed corpus would satisfy you, a plain vector store is the better tool. Use okgv here only when you specifically want generation steered by surfaced near-misses.

---

## When to use `_meta` (and when not)

`_meta` exists for rules that vary *by position in the tree*. If a rule is the same everywhere, it belongs in the schema's global validators, not `_meta`. Decision order:

1. **Is the rule true for every entry regardless of topic?** → Global validator in the schema. Not `_meta`.
2. **Is it structural, "this leaf *is* this label"?** → Encode it in the tree. The leaf path is the rule. Not `_meta`.
3. **Does it narrow a *scalar entry field* for some topics?** (e.g. `basics` allows only `easy`/`medium`.) → `_meta` `entry` namespace. **No code**, okgv auto-enforces it. The field must be a stored attribute or `@property`.
4. **Does it constrain a *compound* field or a *relation between fields* per topic?** (argument signatures, function identity, "the entry must reference the topic's function") → `_meta` `function`/`required`/`optional`/`forbidden` **plus a `validate_for_topic` hook**. okgv does not auto-enforce these because binding them to your entry shape is dataset-specific.
5. **Is it a dedup *policy*, not a constraint?** (overlapping siblings) → `_meta` `similarity_scope` only.

**Do not use `_meta` when:**

- The rule is global → it is just a schema validator, and putting it in every leaf's `_meta` is duplication that the fold cannot help you with.
- The leaf path already encodes the label → restating it in `_meta` is redundant.
- The tree is flat with no per-region rules (Use case 5) → there is nothing to fold.

**The fold is the payoff.** When you *do* use `_meta`, put shared constraints high and specialize low. A signature declared at a parent is inherited by every descendant; a child narrows one field. This is why `_meta` beats per-leaf hardcoding: one declaration, many leaves, and contradictions caught at `create-structure` rather than at generation time. If you find yourself copy-pasting the same `_meta` into sibling leaves, lift it to the parent.

**Two enforcement tiers, restated.** The `entry` namespace is enforced *for you* (tier 1, no code). The argument namespaces (`required`/`optional`/`forbidden`) are *yours to bind* in the hook (tier 2). Reaching for the hook when the `entry` namespace would do is the most common over-engineering; reaching for the `entry` namespace when you actually have a compound argument object is the most common under-reach.

## Choosing similarity scope

| | `leaf` (default) | `subtree` |
|---|---|---|
| Compares against | only the exact target leaf | the target plus all descendants/siblings under the split node |
| Use when | leaves are semantically disjoint (a well-designed tree) | siblings can legitimately produce near-duplicate entries |
| Cost | tracks per-leaf count (cheapest) | tracks subtree count |
| Signal | matches are rejections | cross-leaf matches tagged `sibling: true`, variants, not auto-rejections |

`create-structure` runs a disjointness analysis over the folded specs: provably-disjoint siblings (different `function`, a required key forbidden in the sibling, or validators that narrow to `NEVER`) are noted leaf-safe; siblings it *cannot* prove disjoint produce a warning to set the scope explicitly. Treat that warning as a prompt to decide, not noise. Default to `leaf`; widen to `subtree` only at the node where overlap is real, since the cost is the subtree size.

## Anti-patterns

- **Global rule smeared across every leaf's `_meta`.** If it is the same in all leaves, it is a schema validator. The fold does not deduplicate identical leaf-level blocks for you.
- **A hook for a scalar narrowing.** If you are checking `entry.difficulty in {...}` inside `validate_for_topic`, delete it and use the `entry` namespace, okgv already runs it.
- **Constraining a metadata-only computed value in `entry`.** A value produced only inside `metadata()` is not an entry attribute; `_meta entry` resolves via `getattr` and will report it "not present". Expose it as a `@property` on the entry first.
- **Leaves too coarse.** One giant leaf makes per-topic dedup expensive (brute-force vector scan over the whole leaf) and balance meaningless. Split into finer sub-topics; dedup cost tracks leaf size.
- **Tree that does not match the dedup boundary.** If two leaves should never dedup against each other but currently can, the tree is wrong, fix the partition before reaching for `subtree` scope. `subtree` is for *legitimate* overlap, not for patching a mis-cut tree.
- **`balance_fields` on derived axes.** Balancing a field computed from the topic (e.g. text length, or the leaf name) does nothing useful; balance the independent axes the agent actually controls.

## Quick reference

| Pattern | Tree depth | Per-topic `_meta` | Hook | Scope | Balance |
|---------|-----------|-------------------|------|-------|---------|
| Classification (1) | label = leaf | none | no | `leaf` | secondary field, if any |
| Q&A / difficulty (2) | subject hierarchy | `entry` (optional) | no | `leaf` | `difficulty` |
| Function-calling (3) | function = leaf | `function` + arg namespaces | yes | `leaf` | `difficulty`/etc. |
| RAG eval (4) | doc taxonomy | `similarity_scope` | no | `subtree` | usually none |
| Paraphrase (5) | flat | none | no | `leaf` | none |

The columns are the dials. Most datasets are a point in this space; pick the row closest to yours and adjust. The recurring lesson: push each rule to the surface that owns it, global shape to the schema, partition and balance to the tree, positional constraints to `_meta`, and reach for the hook only when the constraint is compound or relational.
