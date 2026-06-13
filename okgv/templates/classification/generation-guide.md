# Agent Guide

## Goal

Build a labeled dataset of customer-support utterances for fine-tuning an intent
classifier. Each entry is one user message; its intent is the leaf topic it is
filed under (e.g. `billing/refund`), so the topic tree carries the label. Keep
the dataset balanced across the `channel` field, and keep utterances within a
single intent non-duplicative.

Edit `config/structure.json` to match your real intent taxonomy and
`config/schema.py` to match your fields before generating.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Action

After you understood how to use the CLI ask me what I want to do.
