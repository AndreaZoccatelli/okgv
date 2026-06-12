<p align="center">
  <img src="resources/okgv-logo.svg" alt="okgv logo" width="360">
</p>

# okgv - organizing knowledge: graphs and vectors

[![Tests](https://github.com/AndreaZoccatelli/okgv/actions/workflows/tests.yml/badge.svg)](https://github.com/AndreaZoccatelli/okgv/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

LLMs are often used to generate synthetic text datasets for training other ML models. Two requirements make this hard at scale: the dataset has to stay balanced, and it has to avoid near-duplicate instances. Both get harder as the instance count grows.

The reason is context. Suppose you want questions about cats and dogs, each rated easy, medium, or hard, balanced both across categories (cats vs. dogs) and across difficulty levels. The naive approach, asking the LLM to "ensure diversity", forces it to hold every previously generated instance in context to know what is still missing. Deduplication hits the same wall: spotting a near-duplicate requires comparing the candidate against all prior instances, which again means keeping them in context. Past a few hundred instances this becomes infeasible.

okgv moves that state out of the prompt and into storage. It models a dataset as a tree: each topic is a node, its sub-topics are its children, and every instance is an entry attached to a single topic node. Each entry is also stored as a vector embedding. The agent never has to remember what it generated, it queries the store instead.

This makes the agent work one topic at a time. It checks which topics are underrepresented to pick what to generate next, and before adding a new entry it measures the candidate against the entries already under that same topic. The closest matches come back with their full content, so the agent can decide whether the candidate is too similar to keep. The dataset never lives in the prompt, and the result stays easy to inspect.

Handing an agent full ownership of generation requires a degree of trust that isn't always warranted. For that reason, okgv also supports a review stage: entries can be inspected and approved or discarded, interactively through a TUI by a human, or via CLI commands by an agent prompted to act as the reviewer.

## When to use okgv (and when not to)

okgv is not a vector database and not a large-scale curation pipeline. It is a thin, agent-native layer for building a dataset incrementally, where the generating agent makes the novelty and balance decisions in the loop. The design choices follow from that niche.

It is meant to be driven directly by a coding agent. You point the agent at the task; it reads `okgv cli-prompt`, then runs the generation loop itself through the CLI, finding gaps, checking novelty, submitting. You don't build an API-call pipeline, and the agent doesn't have to hold the growing dataset in its context to stay balanced and avoid duplicates, because that state lives in okgv and the agent queries it.

**Use okgv when:**

- An **agent drives generation** and you want it to decide, per candidate, whether a new entry is novel enough to keep, with the nearest existing entries surfaced as full-content context rather than reduced to a similarity score.
- The dataset is naturally **hierarchical** and must stay **balanced** across that hierarchy. The topic tree doubles as the balance stratum and the dedup scope.
- You want a **human or a second agent to review** generated entries before they ship.
- You want **zero infrastructure**: one portable SQLite file, no server, JSON in and out.
- You need the dataset to be an **auditable artifact**: inspectable, reviewable, traceable through the submission log, and reversible with `undo` and `reconcile`. Useful when you have to trust the data, such as eval sets or regulated domains.
- Each **leaf topic stays bounded** (roughly up to a few thousand entries). Similarity is scoped to the exact topic, so its cost tracks the per-topic count, not the total. The overall dataset can be large as long as individual leaf topics stay small. Where possible, group entries into finer sub-topics to keep each leaf small.

**Reach for something else when:**

- You need **reproducible, deterministic** dedup over a fixed corpus. okgv puts the keep/discard call in the agent's hands (see [below](#why-a-guide-not-a-filter)), which is non-deterministic and costs an LLM call per candidate. If you want a repeatable cosine cutoff instead, a vector store with a metadata filter does that without okgv.
- Individual **leaf topics grow very large** (tens of thousands of entries each). sqlite-vec scores vectors by brute force; the per-topic filter bounds how many it scores, but only down to the leaf-topic size, so a single huge topic wants a real ANN index. Splitting it into finer sub-topics is often enough to stay within okgv; if the entries genuinely can't be partitioned, reach for dedicated tooling.
- The data has **no meaningful hierarchy** and balance doesn't matter (for example, a flat set of diverse paraphrases). The tree collapses to a single node, the balance machinery does nothing, and okgv degrades to a dedup wrapper you don't need.
- You want a **full synthetic-data orchestration framework** with provided generation steps and integrations. okgv deliberately stays narrower than that.

In short: okgv trades determinism and per-topic scale for agent-driven, in-the-loop control and zero setup. If that trade matches your workflow, it fits.

### Why a guide, not a filter

A similarity threshold can answer one question: is this candidate too close to something we already have? It returns a number, and a number can reject but it cannot steer. The agent learns only that its attempt failed, not why, so the next attempt is a blind retry that may land in the same crowded region again.

okgv keeps the decision in the loop on purpose. Before a candidate is submitted, `similar` returns the nearest existing entries **with their full content**, not just a score. So "too similar" becomes "too similar *to this specific entry*," and the agent can generate deliberately away from it. A collision stops being a dead end and becomes direction for the next generation.

A threshold is cheaper and deterministic, and for filtering a fixed corpus it is the right tool. But when the goal is to *generate* a balanced, diverse dataset, what matters is filling the gaps, and that needs feedback the agent can act on. Showing it the nearest existing entry turns each near-miss into a more informed next attempt.

## Quickstart

```bash
pip install -e ".[embeddings]"
cd my-dataset-project
okgv init
```

`okgv init` scaffolds a project you fill in (existing files are never overwritten):

| File | What it is | You edit it… |
|------|-----------|--------------|
| `.env` | Config: schema specifier, DB path, embedding model, review mode | by hand, set `OKGV_SCHEMA` and `EMBED_MODEL` |
| `config/schema.py` | Entry schema template (`MyEntrySchema`): fields, validators, DB mapping | by hand, or hand to an agent with `prompts/schema-guide.md` |
| `config/structure.json` | Topic hierarchy as nested JSON (`{}` = leaf) | by hand, or hand to an agent with `prompts/structure-prompt.md` |
| `generation-guide.md` | The brief an agent reads to generate entries. Has a `TODO` goal for you to fill | by hand, describe your dataset's goal |
| `prompts/schema-guide.md` | Guide an agent follows to design `config/schema.py` with you | leave as-is |
| `prompts/structure-prompt.md` | Guide an agent follows to design `config/structure.json` with you | leave as-is |
| `prompts/reviewer-prompt.md` | Guide a reviewer agent follows to approve/reject queued entries | leave as-is |

The three `prompts/` files are the point of okgv: each hands one phase of the work to a coding agent. You don't write the schema, the structure, or the entries yourself, you point an agent at the matching guide and it drives the CLI.

**1. Design the schema** (what every entry looks like):

```bash
"read prompts/schema-guide.md and help me design my schema" # prompt for the agent
```

The agent interviews you about fields, constraints, and storage, then writes `config/schema.py`. Set `OKGV_SCHEMA=config.schema:YourSchema` in `.env`.

**2. Design the topic tree** (how entries are scoped and balanced):

```bash
"read prompts/structure-prompt.md and help me design my topic structure"
okgv create-structure --file config/structure.json   # load the agreed tree into okgv.db
```

**3. Generate entries** (fill in `generation-guide.md`'s goal first):

```bash
"read generation-guide.md and start generating"
```

The agent runs the loop itself: `cli-prompt` to learn the CLI, find gaps, check novelty with `similar`, submit. The dataset never lives in its context.

**4. Review** (optional, if `OKGV_REVIEW=all` or `--review` was used):

```bash
"read prompts/reviewer-prompt.md and review the pending queue"   # agent reviewer
okgv review -i                                                          # or human TUI
```

**5. Export for training**:

```bash
okgv export --output dataset.jsonl --exclude-in-review
okgv export --output dataset.jsonl --split "train=0.8,val=0.1,test=0.1"   # stratified splits
```

One JSONL file, or one per split. `--split` divides each topic × balance-field stratum by the given fractions, so train/val/test all keep the dataset's distribution. Preview with `--dry-run` to see per-split counts and balance before writing.

See [`example/`](example/) for a complete worked project: a filled-in schema, topic structure, generation guide, and a populated database.

## How it works

Everything lives in one portable SQLite file (`okgv.db`): the topic tree, entries, their vector embeddings (via [sqlite-vec](https://github.com/asg017/sqlite-vec)), the submission log, and the review queue. No server, zero setup.

Topics form a path-identified tree (`algebra/linear_algebra/basics`). The tree is both the **balance stratum** and the **dedup scope**: counts and stats are recursive across descendants, but similarity search is scoped to the exact target topic, so its cost tracks the per-topic count, not the dataset total. An agent works one topic at a time, queries `least-topic` to find gaps, and checks `similar` (full-content, not a score) before submitting.

See [Architecture & Internals](docs/architecture.md) for the details: topic structure, similarity scoping, session logging, and reliability.

## Documentation

| Doc | Contents |
|-----|----------|
| [Commands](docs/commands.md) | Full command reference, examples, agent workflow, error handling |
| [Entry Schema & Configuration](docs/schema.md) | Install, env vars, embedding backends, defining a schema, validators, field descriptions, balance fields |
| [Review System](docs/review.md) | Review modes, CLI and TUI workflows, review states |
| [Architecture & Internals](docs/architecture.md) | Storage, topic structure, similarity scoping, session logging, reliability |
