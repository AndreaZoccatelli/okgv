<p align="center">
  <img src="../resources/okgv-logo-light.svg#gh-light-mode-only" alt="okgv logo" width="400">
  <img src="../resources/okgv-logo-dark.svg#gh-dark-mode-only" alt="okgv logo" width="400">
</p>

# Design Principles

This document is about *how to think* when shaping an okgv dataset — how deep to make the tree, what a good partition is, when a dimension belongs in the structure versus in a field. [Dataset Patterns](patterns.md) gives concrete recipes; this gives the reasoning the recipes are derived from. If you internalize the principles here, the patterns become obvious and you can design shapes the catalog does not list.

## The one idea

okgv exists to move dataset state **out of the prompt and into storage**, so a generating agent decides what to add next by *querying* rather than *remembering*. Everything else follows from that. The structure you design is the agent's externalized memory: it is the index it consults to answer two questions on every iteration — *what is underrepresented?* and *is this candidate too similar to what already exists?* A good structure makes both questions cheap and well-posed; a bad one makes them ambiguous or expensive.

So the design task is not "model my domain." It is "build the index that lets an agent stay balanced and non-redundant without holding the dataset in context." Those are different goals, and the difference is the source of most of the principles below.

## Principle 1 — One tree, three jobs

The topic tree is not just organization. The same tree is simultaneously:

1. the **partition** — every entry lands in exactly one leaf;
2. the **balance stratum** — `least-topic` and `report` measure coverage across it;
3. the **dedup scope** — `similar` compares a candidate only within its topic (leaf by default).

These are not three features you tune independently. They are **one structure read three ways**. When you move a boundary in the tree, you move all three at once: you change which entries are grouped, which counts compete for "least", and which entries a candidate is checked against. This coupling is the central fact of okgv design. Most mistakes are an attempt to optimize one of the three while ignoring what the change does to the other two.

The practical consequence: never ask "where should this entry go organizationally." Ask "which entries should this one be balanced against and deduped against." Those entries are its leaf.

## Principle 2 — The leaf is the comparison unit

A leaf is the finest bucket, and it is the unit of both balance and dedup. The single question that sizes a leaf correctly:

> **Within this leaf, are any two entries meaningfully comparable — such that "too similar" is a real judgment and "we have enough" is a real quota?**

If yes, the leaf is right. The two failure modes are symmetric:

- **Leaf too broad.** Entries inside it are not really comparable (a leaf called `math` holds algebra and calculus). Dedup becomes noise — the nearest neighbor is "close" only because everything in the bucket is loosely related, so the signal that guides the agent away from redundancy is gone. Balance is also meaningless: the count is high, but high in *what*?
- **Leaf too narrow.** Each leaf holds a handful of entries. The balance stratum fragments into cells too small to be a quota, the agent thrashes between many near-empty buckets, and you have spent structure on distinctions that do not change what "similar" means.

There is also a hard cost ceiling. Dedup is a brute-force vector scan **scoped to the leaf**, so its cost tracks the per-leaf entry count, not the dataset total. This is by design — it is what lets the total dataset grow large while each similarity check stays cheap — but it means a leaf has a natural size budget (roughly up to a few thousand entries). A leaf that blows past it is a signal to split, not to accept slow checks. The corollary: **the dataset scales by adding leaves, not by growing them.**

## Principle 3 — Depth is the number of dimensions you want separately controlled

"How deep should the tree be?" has a precise answer: **as deep as the number of independent dimensions along which you want entries separately scoped and separately quota'd — no deeper.**

Each level of the tree is a dimension that gets its own dedup scope and its own balance cell per value. Adding a level buys you two things for that dimension: similarity is computed *within* each value (so variants across values are not treated as duplicates), and `report` tracks coverage *per* value (so you can demand a quota in each). It costs you a multiplication in the number of cells to fill.

That cost is the discipline. The balance unit okgv actually tracks is the **leaf × balance-field-value cell** (this is exactly what `report` enumerates, empty cells included). So:

> total cells ≈ (number of leaves) × (product of balance-field cardinalities)
>
> number of leaves ≈ product of the branching factors down the tree

Every level you add multiplies the leaf count; every balance field multiplies the within-leaf cells. Each cell needs enough entries to be a meaningful stratum. Your **total entry budget** therefore bounds how fine you can go: pick depth and fields so that `total_budget / total_cells` is comfortably more than a handful. If that ratio drops near one, you have over-stratified — entries are spread so thin that "balance" is just "one of everything," and the agent spends its run filling singleton cells instead of building density.

So the depth decision is really a budget decision. Shallow tree + a couple of balance fields covers most needs. Reach for another level only when a dimension genuinely needs its *own dedup scope* (Principle 5), not merely its own count.

## Principle 4 — Mutually exclusive, collectively exhaustive (MECE)

Siblings should partition their parent: **no overlap, no gaps.**

- **Mutually exclusive** is a dedup requirement, not a tidiness preference. If two siblings can hold the same entry, then under default leaf scope the agent dedups a candidate against only one of them and misses the duplicate in the other. Overlapping siblings silently defeat the dedup the whole tool exists to provide. (When overlap is genuinely unavoidable, that is what `similarity_scope: subtree` is for — but treat it as the exception that proves the rule, not the default escape hatch. Fix the partition first.)
- **Collectively exhaustive** is a coverage requirement. If the siblings under a parent do not cover the parent's full scope, there are entries with no correct leaf to land in. The agent either forces them into an ill-fitting leaf (poisoning that leaf's dedup scope) or cannot place them at all.

A useful test for a proposed split: *could a reasonable entry belong to two of these siblings, or to none of them?* If yes, the split is wrong — merge the overlapping ones, add the missing one.

## Principle 5 — A dimension is either a branch or a field, and the choice is about scope

You have a categorical dimension (difficulty, language, channel, function). Does it become a **level in the tree** or a **balance field on the entry**?

Decide by what the dimension does to *meaning*:

- **Make it a branch** when its values define **distinct semantic scopes** — when two entries that differ only on this dimension should *not* be deduped against each other, because they legitimately occupy different regions. A query that maps to `get_weather` and one that maps to `send_message` are not near-duplicates even if worded alike; `function` is a branch. Also make it a branch when you need a **hard per-value quota** (a guaranteed N entries for each value) or when the dimension carries **per-value constraints** (`_meta`).
- **Make it a field** when its values are an **orthogonal attribute** that does not change what "similar" means — when two entries differing only on this dimension *could* be near-duplicates you want caught. Difficulty is usually a field: an easy and a hard phrasing of the same question are redundant, and you want `similar` to see across difficulty, so difficulty must *not* split the dedup scope. You still balance it, via `balance_fields`.

The litmus question: **"Should two entries that differ only on this dimension ever be flagged as duplicates of each other?"** If they should be flagged → field (keep them in the same dedup scope). If they should not → branch (separate the scopes).

There is also the combinatorial cost from Principle 3: branches multiply the leaf count, fields multiply within-leaf cells, and the product is your cell budget. Two dimensions as nested branches give you their product of leaves; one branch and one field give you the same number of cells but a *shallower* tree with larger, more comparable leaves — usually the better trade unless both dimensions truly need separate scopes.

## Principle 6 — Constraints narrow position; they do not define entries

The entry **schema** defines what every entry is, everywhere. `_meta` exists only for rules that **vary by position in the tree**, and it is strictly monotone down a path: a child may narrow a constraint, add one, or forbid a key — never relax. Identity (`function`) is set once on a path; policy (`similarity_scope`) is nearest-ancestor.

Two implications shape how you author it:

- **Push shared constraints high, specialize low.** Because the fold inherits down the path, a constraint declared at a parent applies to every descendant. Declaring the common shape once at the parent and narrowing one field at a child is the idiom; copy-pasting the same block into sibling leaves is the anti-idiom. If you are repeating `_meta`, lift it.
- **Contradictions are caught at ingest, not at generation.** A narrowing that cannot be satisfied, a redeclared identity, a malformed validator — all fail at `create-structure`, before any entry exists. This is deliberate: the structure is validated as a *design artifact*. Treat a `create-structure` warning (a topic with no `_meta` on its path, non-disjoint siblings with no explicit scope) as a design review, not noise.

When `_meta` is worth using at all, and which namespace to reach for, is the subject of [Dataset Patterns](patterns.md#when-to-use-_meta-and-when-not). The principle here is only: `_meta` is the *positional* layer, the schema is the *universal* layer, and you should be able to say which one any given rule belongs to before you write it.

## Principle 7 — Design for the agent's decision, not the librarian's catalog

okgv keeps the keep/discard call **in the agent's loop** on purpose: `similar` returns the nearest entries with full content, so a near-miss becomes direction for the next attempt rather than an opaque rejection. Your structure should make that in-loop decision well-posed.

Concretely: a leaf is the question "is this candidate too similar to *these specific entries*?" If the leaf is coherent (Principle 2) and exclusive (Principle 4), that question has a clean answer the agent can act on. If the leaf is a grab-bag, the nearest neighbor is uninformative and the feedback loop degrades. So you are not building a taxonomy for a human to browse — you are building the set of well-scoped local questions the agent will answer thousands of times. Judge every structural choice by whether it sharpens or blurs that local question.

## Sizing and evolution

You do not have to get the structure right up front, and you should not try to.

- **Start shallow and broad.** A few top-level categories, one or two levels, the balance fields you know you need. Resist depth you cannot yet justify by Principle 5.
- **Let the tools tell you where to refine.** Run a batch, then read `report`: if a leaf is overflowing its size budget (Principle 2) or you discover a dimension inside it that you now want quota'd, split it. If many cells sit empty because you over-stratified (Principle 3), merge.
- **The structure is not frozen.** `move-topic` and `move-entry` reshape it and revalidate moved entries against their new position; a "refinement split" turns a constrained leaf into narrowing children, each with its own quota and its own `entry-prompt --topic`; `revalidate` finds entries left invalid by a tightened spec. Restructuring is a supported operation, not a recovery from failure.
- **Keep depth balanced across branches.** Because `least-topic` compares the *direct children* of a node by recursive count, wildly asymmetric depth makes those comparisons meaningless — a leaf sibling competing against a deep subtree is one bucket competing against an aggregate of many. Roughly even depth (±1 level) keeps sibling comparison, and therefore the balance signal, honest.

The throughline: shape the tree to the *dimensions you will actively balance and dedup along*, size leaves to the *comparison you want to be meaningful*, and let the dataset's own coverage gaps — surfaced by `report` — drive every later refinement.

## Tensions to hold

Good design here is balancing forces, not maximizing one. The recurring tensions:

| Tension | Pulls toward more depth/granularity | Pulls toward less |
|---|---|---|
| **Dedup precision vs. fill density** | finer leaves → sharper similarity signal | finer leaves → thinner cells, more empty quota |
| **Separate scope vs. cross-checking** | branch a dimension → variants not flagged as dups | field a dimension → near-dups caught across its values |
| **Per-value quota vs. comparable leaves** | branch → guaranteed coverage per value | field → larger, more comparable leaves, shallower tree |
| **Positional rules vs. one schema** | more `_meta` → precise per-topic contracts | less `_meta` → simpler, uniform validation |

None of these has a fixed answer; each is resolved against *this* dataset's budget and goals. The principles tell you which force you are trading off, so the trade is deliberate rather than accidental.
