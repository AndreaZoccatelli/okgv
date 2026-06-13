# Agent Guide

## Goal

Build a retrieval-evaluation set of (query, passage) pairs over a document
taxonomy, used to test a retriever. Each entry is a search query and the passage
that correctly answers it. Sibling topics can overlap, so `config/structure.json`
sets `similarity_scope: subtree` on the shared parent and dedup runs across the
whole subtree: avoid generating a query that duplicates one already filed under a
nearby leaf (matches in other leaves come back tagged `sibling: true`).

Edit `config/structure.json` to mirror your real document taxonomy before
generating.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Action

After you understood how to use the CLI ask me what I want to do.
