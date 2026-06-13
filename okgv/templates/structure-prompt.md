# Structure Design Guide

You are helping the user design the topic hierarchy for their knowledge base. The structure determines how entries are organized and scoped for similarity search.

## Key Principles

- **Mutually exclusive**: sibling topics must not overlap. An entry should belong to exactly one leaf topic.
- **Collectively exhaustive**: siblings should cover the full scope of their parent.
- **Balanced depth**: aim for similar depth across branches. Avoid one branch with 5 levels while another has 1.
- **Leaf topics hold entries**: intermediate topics are just organizational. Keep leaf topics narrow enough that entries within them are meaningfully comparable.

## Format

The structure is a nested JSON object. Keys are topic names (snake_case), values are dicts of subtopics. Empty dict `{}` marks a leaf.

```json
{
  "algebra": {
    "linear_algebra": {
      "systems_of_equations": {},
      "matrix_operations": {},
      "vector_spaces": {}
    },
    "abstract_algebra": {
      "group_theory": {},
      "ring_theory": {}
    }
  }
}
```

## Optional: node constraints (`_meta`)

A topic node may carry a reserved `_meta` key describing constraints on the entries placed under it (any other key is a child topic). This is optional — a structure with no `_meta` behaves exactly as before.

```json
{
  "weather": {
    "current_conditions": {
      "_meta": {
        "function": "get_current_weather",
        "required": {"location": "not_empty"},
        "optional": {"units": {"one_of": ["celsius", "fahrenheit"]}},
        "similarity_scope": "leaf"
      }
    }
  }
}
```

A validator is written in one of three forms (`field` always defaults to the key):

- **Bare tag string** for a zero-arg validator — `"location": "not_empty"`.
- **Tagged `{tag: args}`** — `{"one_of": ["celsius", "fahrenheit"]}`, `{"in_range": [0, 1]}`, `{"is_type": ["int", "float"]}`, `{"matches": "^[A-Z]"}`. The value is the validator's argument(s): a single-argument validator takes the value whole (so `one_of`'s value is the list of allowed values); a multi-argument validator like `in_range` takes a positional list `[lo, hi]`; a dict value is read as named args (`{"in_range": {"lo": 0, "hi": 1}}`).
- **Explicit `{"type": tag, ...}`** — always works, needed for validators without a shorthand (e.g. `items`).

A **list is always a conjunction** — every validator in it runs: `"x": ["not_empty", {"matches": "^[A-Z]"}]`.

Built-in tags:

- `"not_empty"` — non-empty string
- `{"one_of": [<values>]}` — value in the allowed set
- `{"in_range": [<lo>, <hi>]}` — number within `[lo, hi]`
- `{"is_type": [<types>]}` — instance of a type; names: `dict`, `list`, `str`, `int`, `float`, `bool`
- `{"matches": "<regex>"}` — full-match a regex
- `{"type": "items", "inner": {...}, "min_len": N}` — list with a per-element validator (no shorthand)

Your project may register more. **Run `okgv validators` to list every tag available here (built-in + custom)** before authoring `_meta`. If you need a constraint that no available tag covers, that is a **schema change, not a structure change**: define a custom validator in `config/validators.py` (see the Schema Design Guide), add its module to `OKGV_VALIDATORS`, then re-run. Authoring an unregistered tag fails at `create-structure` with `unknown validator tag`.

### Populating a constraint

You can't infer constraints from topic names — the entries don't exist yet. The constraint *values* come from what you were given: the **entry schema** (`okgv entry-prompt`) and, for tool-use datasets, the **function signatures** (e.g. in `generation-guide.md`). Your job is to **transcribe a known field shape into a validator**, using this mapping:

| The field/parameter is… | Write |
|---|---|
| one of a fixed set of values | `{"one_of": [<values>]}` |
| a number within a range | `{"in_range": [<lo>, <hi>]}` |
| a specific type | `{"is_type": ["int"]}` (`str`, `int`, `float`, `bool`, `list`, `dict`) |
| a non-empty string | `"not_empty"` |
| a string matching a pattern | `{"matches": "<regex>"}` |
| a **flat list** of one of the above | `{"type": "items", "inner": <validator for each element>}` |
| an **object with named keys** (a function's arguments) | the `required`/`optional`/`forbidden` keys, one validator per key |

`items` is for a list whose elements all share one rule; its `inner` is written like any other validator (a tag string, a `{tag: args}`, or explicit). For example, `attendees: list of non-empty strings` from a signature becomes:

```json
"attendees": {"type": "items", "inner": "not_empty", "min_len": 1}
```

If a field's shape isn't in this table (a nested object, a bespoke format), don't approximate it — that's a custom validator in `config/validators.py`.

- `_meta` blocks **compose along a path**: a child's effective spec is the fold of every ancestor's `_meta` plus its own. A child may narrow an existing constraint, add a new one, or `forbid` a key — never relax one. A contradiction or a malformed validator fails at `create-structure`, before anything is written.
- `entry` narrows global entry-schema fields (e.g. `difficulty`); `required`/`optional`/`forbidden` constrain a function's arguments; `function` sets the function identity (once per path).
- `similarity_scope` is `"leaf"` (default) or `"subtree"`: under `subtree`, `similar` also searches sibling topics and reports cross-topic matches as variants.
- Splitting a constrained topic into children that narrow it (a "refinement split") gives each child its own quota and its own `entry-prompt --topic` rendering. Use it when you want targeted generation, not for every metadata dimension.
- `create-structure` warns about topics with no `_meta` on their path and about overlapping siblings with no explicit `similarity_scope`; run `entry-prompt --topic <path>` to see a topic's folded effective spec. Entries can only be submitted to leaf topics.

## How to Build It

### Option A: User gives a broad topic

1. Ask the user for the top-level subject and desired depth/granularity
2. Break it into 3–7 mutually exclusive top-level categories
3. Subdivide each category into subtopics, repeating until leaf topics are narrow enough for meaningful similarity comparison
4. Present the structure as JSON and as a tree for readability
5. Iterate with the user — merge topics that are too narrow, split ones that are too broad

### Option B: User provides a resource

The user may pass a document, table of contents, syllabus, taxonomy, or any structured reference. Extract topics from it:

1. Identify the natural groupings in the resource
2. Map them to a hierarchical structure, normalizing names to snake_case
3. Merge overlapping sections, split overly broad ones
4. Fill gaps — if the resource is incomplete, suggest additions to make siblings exhaustive
5. Present the structure and iterate

## Validation Checklist

Before finalizing, verify:

- [ ] No two siblings overlap in scope
- [ ] Every branch ends in leaf topics (empty dicts)
- [ ] Leaf topics are specific enough that entries within them are comparable
- [ ] Topic names are snake_case, concise, and descriptive
- [ ] Depth is roughly balanced across branches (±1 level is fine)
- [ ] Total leaf count is reasonable for the dataset size (a few entries per leaf minimum)

## Output

Save the final structure to `config/structure.json`, then create it:

```bash
okgv create-structure --file config/structure.json
okgv get-structure  # verify it loaded correctly
```

## Interact with User

Present the proposed structure as both a tree and JSON. Ask the user to confirm or request changes before creating it.
