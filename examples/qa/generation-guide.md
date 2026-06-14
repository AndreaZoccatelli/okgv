# Agent Guide

## Goal

Build a math question-answer dataset for evaluation, spanning algebra and
calculus. Each entry is a self-contained question with its full worked answer,
graded `easy`/`medium`/`hard`. Keep every subject × difficulty cell balanced
(use `okgv report` to find gaps) and avoid near-duplicate questions within a
leaf. Some leaves restrict which difficulties they accept, via `_meta` in
`config/structure.json`; okgv enforces that for you on submit.

Edit `config/structure.json` to match your real subject taxonomy before
generating.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Action

After you understood how to use the CLI ask me what I want to do.
