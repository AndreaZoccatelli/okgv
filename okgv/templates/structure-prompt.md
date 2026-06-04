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
