"""Cosmetic visualizations for the example datasets.

NOT part of okgv, this is example cosmetics only. Each function reads an
example's exported ``*.jsonl`` and renders a chart into ``<example>/media/`` so
the example READMEs can *show* that the agent-built dataset stayed balanced
(``balance_grid``) and free of near-duplicates (``novelty_hist`` — the rigorous,
projection-free proof; ``diversity_map`` is an illustrative scatter), the two
things okgv exists to keep true. ``knowledge_tree`` renders the topic tree
itself as a sunburst, sized by entry counts.

Usage::

    pip install -r requirements.txt
    python viz.py classification        # render one example's charts
    python viz.py all                   # render every example

Charts per example are wired in ``EXAMPLES`` below.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).parent

# example name -> what to render. `balance_field` None means "no balance axis"
# (coverage only); `splits` replaces `dataset` for stratified exports.
# Per example: dataset(s), the balance axis, the text to embed, scatter colour
# depth, and `dedup_depth` = the scope okgv deduped within (None = the full leaf
# topic; an int = the first N path segments, for subtree scope).
EXAMPLES: dict[str, dict] = {
    "classification": {
        "dataset": "dataset.jsonl",
        "balance_field": "channel",
        "text_field": "text",
        "color_depth": 1,
        "dedup_depth": None,
    },
    "function-calling": {
        "splits": {"train": "dataset-train.jsonl", "val": "dataset-val.jsonl", "test": "dataset-test.jsonl"},
        "balance_field": "difficulty",
        "text_field": "query",
        "color_depth": 1,
        "dedup_depth": None,
    },
    # rag's topics all sit under networking/ and dedup is subtree-scoped, so
    # colour the scatter by sub-topic and measure novelty across the subtree.
    "rag": {
        "dataset": "dataset.jsonl",
        "balance_field": None,
        "text_field": "query",
        "color_depth": 2,
        "dedup_depth": 1,
    },
}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _group(topic: str, depth: int = 1) -> str:
    """First ``depth`` segments of a topic path, used to colour the diversity scatter."""
    return "/".join(topic.split("/")[:depth])


def balance_grid(entries: list[dict], balance_field: str, out: Path, title: str) -> None:
    """Heatmap of entry counts per (leaf topic x balance value): the balance thesis."""
    topics = sorted({e["topic"] for e in entries})
    values = sorted({str(e.get(balance_field, "")) for e in entries})
    counts = Counter((e["topic"], str(e.get(balance_field, ""))) for e in entries)
    matrix = [[counts[(t, v)] for v in values] for t in topics]
    hi = max((c for row in matrix for c in row), default=1)

    fig, ax = plt.subplots(figsize=(max(4.0, 1.1 * len(values) + 2), max(3.0, 0.34 * len(topics) + 1)))
    im = ax.imshow(matrix, aspect="auto", cmap="Blues", vmin=0)
    ax.set_xticks(range(len(values)), values)
    ax.set_yticks(range(len(topics)), topics, fontsize=8)
    for i in range(len(topics)):
        for j in range(len(values)):
            c = matrix[i][j]
            ax.text(j, i, str(c), ha="center", va="center", fontsize=7, color="white" if c > hi / 2 else "black")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="entries")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def topic_coverage(entries: list[dict], out: Path, title: str) -> None:
    """Horizontal bar of entries per leaf topic, sorted: shows fill and gaps."""
    items = sorted(Counter(e["topic"] for e in entries).items(), key=lambda kv: kv[1])
    labels = [k for k, _ in items]
    vals = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(7.0, max(3.0, 0.32 * len(labels) + 1)))
    ax.barh(range(len(labels)), vals, color="#4C78A8")
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.set_xlabel("entries")
    ax.set_title(title)
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v}", va="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def embed(entries: list[dict], text_field: str) -> np.ndarray:
    """Unit-normalized MiniLM embeddings (same model okgv defaults to), so a dot
    product is cosine similarity. Shared by novelty_hist and diversity_map."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return np.asarray(
        model.encode([e[text_field] for e in entries], show_progress_bar=False, normalize_embeddings=True)
    )


def novelty_hist(
    entries: list[dict], vecs: np.ndarray, out: Path, title: str, scope_depth: int | None = None
) -> tuple[float, float]:
    """Histogram of each entry's nearest-neighbour cosine similarity *within okgv's
    dedup scope*: the direct, projection-free proof of the dedup thesis, since okgv
    scopes its `similar` check to exactly this region (the leaf, or a subtree when
    ``scope_depth`` is set). The bulk sitting well below 1.0 = entries are distinct,
    not near-duplicates. Returns (median, max) of the nearest-neighbour similarities."""
    by_topic: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        scope = e["topic"] if scope_depth is None else _group(e["topic"], scope_depth)
        by_topic[scope].append(i)

    nearest: list[float] = []
    for idxs in by_topic.values():
        if len(idxs) < 2:
            continue  # a lone entry has no in-topic neighbour
        sim = vecs[idxs] @ vecs[idxs].T
        np.fill_diagonal(sim, -1.0)  # exclude self
        nearest.extend(sim.max(axis=1).tolist())
    arr = np.asarray(nearest) if nearest else np.zeros(1)
    median, peak = float(np.median(arr)), float(arr.max())

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    ax.hist(arr, bins=40, range=(0.0, 1.0), color="#4C78A8", edgecolor="white")
    top = ax.get_ylim()[1]
    ax.axvline(median, color="#54A24B", linewidth=1.5)
    ax.text(median, top * 0.94, f" median {median:.2f}", color="#54A24B", fontsize=8, va="top")
    ax.axvline(peak, color="#E45756", linestyle="--", linewidth=1.2)
    ax.text(peak, top * 0.82, f" closest pair {peak:.2f}", color="#E45756", fontsize=8, va="top", ha="right")
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("nearest-neighbour cosine similarity within the same topic")
    ax.set_ylabel("entries")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return median, peak


def diversity_map(entries: list[dict], vecs: np.ndarray, out: Path, title: str, color_depth: int = 1) -> None:
    """2-D PCA of the entry embeddings, coloured by topic group. A "diversity
    feel" view, not the dedup proof: PCA is linear and dedup is scoped per leaf,
    so related domains can overlap here (see novelty_hist for the actual claim)."""
    from sklearn.decomposition import PCA

    xy = PCA(n_components=2, random_state=0).fit_transform(vecs)
    groups = [_group(e["topic"], color_depth) for e in entries]
    uniq = sorted(set(groups))
    cmap = plt.get_cmap("tab10" if len(uniq) <= 10 else "tab20")

    fig, ax = plt.subplots(figsize=(7.0, 6.0))
    for k, g in enumerate(uniq):
        idx = [i for i, gg in enumerate(groups) if gg == g]
        ax.scatter(xy[idx, 0], xy[idx, 1], s=14, color=cmap(k % cmap.N), label=g, alpha=0.7, linewidths=0)
    ax.legend(fontsize=8, markerscale=1.4, loc="best", frameon=False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def _lighten(rgb: tuple[float, ...], amount: float) -> tuple[float, float, float]:
    """Blend an RGB colour toward white by ``amount`` (0 = unchanged, 1 = white)."""
    r, g, b = rgb[:3]
    return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount)


def knowledge_tree(entries: list[dict], out: Path, title: str) -> None:
    """Sunburst of the topic tree: each ring is a depth level, each wedge's angle is
    proportional to how many entries sit under it, and colour groups everything by
    top-level domain (lighter toward the leaves). One glance shows the tree's shape
    *and* how the agent's entries are distributed across it."""
    counts = Counter(e["topic"] for e in entries)
    paths = [t.split("/") for t in counts]
    max_depth = max(len(p) for p in paths)

    level_counts: list[defaultdict[tuple[str, ...], int]] = [defaultdict(int) for _ in range(max_depth)]
    for parts, c in zip(paths, counts.values()):
        for d in range(len(parts)):
            level_counts[d][tuple(parts[: d + 1])] += c

    angle_ranges: dict[tuple[str, ...], tuple[float, float]] = {(): (0.0, 2 * np.pi)}
    for d in range(max_depth):
        parents = sorted({prefix[:-1] for prefix in level_counts[d]})
        for parent in parents:
            start, end = angle_ranges[parent]
            children = sorted(p for p in level_counts[d] if p[:-1] == parent)
            ctotal = sum(level_counts[d][c] for c in children)
            cur = start
            for c in children:
                width = (end - start) * level_counts[d][c] / ctotal
                angle_ranges[c] = (cur, cur + width)
                cur += width

    domains = sorted({p[0] for p in level_counts[0]})
    cmap = plt.get_cmap("tab10" if len(domains) <= 10 else "tab20")
    domain_color = {dom: cmap(i % cmap.N)[:3] for i, dom in enumerate(domains)}

    inner_radius = 0.3
    fig, ax = plt.subplots(figsize=(8.0, 8.0), subplot_kw={"projection": "polar"})
    for d in range(max_depth):
        for prefix, count in sorted(level_counts[d].items()):
            start, end = angle_ranges[prefix]
            width = min(end - start, 2 * np.pi - 1e-3)  # a full-circle wedge renders as a disc otherwise
            color = _lighten(domain_color[prefix[0]], min(0.65, d * 0.22))
            ax.bar(
                (start + end) / 2,
                1.0,
                width=width,
                bottom=inner_radius + d,
                color=color,
                edgecolor="white",
                linewidth=0.6,
                align="center",
            )
            if width > 0.05:
                angle_deg = np.degrees((start + end) / 2)
                rotation = angle_deg if angle_deg <= 90 or angle_deg >= 270 else angle_deg + 180
                ax.text(
                    (start + end) / 2,
                    inner_radius + d + 0.5,
                    f"{prefix[-1]} ({count})" if d == max_depth - 1 or width > 0.3 else prefix[-1],
                    rotation=rotation,
                    rotation_mode="anchor",
                    ha="center",
                    va="center",
                    fontsize=8 if d == 0 else 6.5,
                    fontweight="bold" if d == 0 else "normal",
                )
    ax.set_ylim(0, inner_radius + max_depth)
    ax.axis("off")
    ax.set_title(title)
    ax.text(0, 0, f"{sum(counts.values())}\nentries", ha="center", va="center", fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def split_balance(splits: dict[str, list[dict]], balance_field: str, out: Path, title: str) -> None:
    """Grouped bars of each split's balance-value proportions: stratified
    train/val/test keep the dataset's distribution."""
    values = sorted({str(e.get(balance_field, "")) for entries in splits.values() for e in entries})
    names = list(splits)
    width = 0.8 / len(names)

    fig, ax = plt.subplots(figsize=(max(5.0, 1.2 * len(values) + 2), 4.0))
    for k, name in enumerate(names):
        counts = Counter(str(e.get(balance_field, "")) for e in splits[name])
        total = sum(counts.values()) or 1
        props = [counts[v] / total for v in values]
        ax.bar([j + k * width for j in range(len(values))], props, width, label=f"{name} ({sum(counts.values())})")
    ax.set_xticks([j + 0.4 - width / 2 for j in range(len(values))], values)
    ax.set_ylabel("proportion")
    ax.set_title(title)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def render(name: str, base: Path = HERE) -> Path:
    cfg = EXAMPLES[name]
    example_dir = base / name
    media = example_dir / "media"
    media.mkdir(exist_ok=True)

    if "splits" in cfg:
        splits = {k: load(example_dir / v) for k, v in cfg["splits"].items()}
        entries = [e for group in splits.values() for e in group]
    else:
        splits = None
        entries = load(example_dir / cfg["dataset"])

    if cfg["balance_field"]:
        balance_grid(entries, cfg["balance_field"], media / "balance.png", f"{name}: {cfg['balance_field']} x topic")
    topic_coverage(entries, media / "coverage.png", f"{name}: entries per topic")
    knowledge_tree(entries, media / "tree.png", f"{name}: knowledge tree")

    vecs = embed(entries, cfg["text_field"])
    median, peak = novelty_hist(
        entries,
        vecs,
        media / "novelty.png",
        f"{name}: nearest-neighbour similarity within dedup scope",
        scope_depth=cfg.get("dedup_depth"),
    )
    diversity_map(
        entries,
        vecs,
        media / "diversity.png",
        f"{name}: embedding diversity (PCA)",
        color_depth=cfg.get("color_depth", 1),
    )
    if splits:
        split_balance(splits, cfg["balance_field"], media / "splits.png", f"{name}: balance preserved across splits")
    print(f"  intra-topic nearest-neighbour similarity: median {median:.2f}, max {peak:.2f}")
    return media


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=[*EXAMPLES, "all"], help="example to render (or 'all')")
    args = parser.parse_args()
    names = list(EXAMPLES) if args.example == "all" else [args.example]
    for name in names:
        media = render(name)
        print(f"{name}: wrote charts to {media}")


if __name__ == "__main__":
    main()
