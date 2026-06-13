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
        "optional": {"units": ["celsius", "fahrenheit"]},
        "similarity_scope": "leaf"
      }
    }
  }
}
```

Authoring shorthands keep blocks terse (the explicit long form is always accepted too):

- A bare tag string — `"location": "not_empty"` — is the validator with its `field` defaulted to the key.
- A list of strings — `"units": ["celsius", "fahrenheit"]` — is a `OneOf` over those values.
- The `field` inside any validator object defaults to its key, so `{"type": "is_type", "expected": ["int"]}` is enough; a list that contains a validator object is a conjunction (all run). The explicit `{"type": "not_empty", "field": "location"}` form still works.

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
