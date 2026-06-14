# Agent Guide

## Goal

Build a diverse pool of paraphrases or stylistic variants of seed sentences.
Each entry is one variant, filed under its seed topic. There is no hierarchy to
balance; the goal is maximal diversity, so check `okgv similar` before every
submission and deliberately steer each new variant away from the nearest
existing one.

Edit `config/structure.json` to list your real seeds (one leaf per seed) before
generating. Note: this is the thinnest use of okgv (dedup feedback only); if a
fixed cosine cutoff would do, a plain vector store may suit you better.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Action

After you understood how to use the CLI ask me what I want to do.
