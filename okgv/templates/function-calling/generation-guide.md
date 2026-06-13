# Agent Guide

## Goal

Build a tool-use (function-calling) dataset: each entry is a user query paired
with the correct function call, a function name plus an arguments object. The
topic tree groups functions by domain, and each leaf declares its function
identity and argument signature via `_meta` in `config/structure.json` (folded
root-to-leaf). Generate queries that map unambiguously to the leaf's function
and produce arguments that satisfy its signature; okgv rejects an entry whose
function or arguments do not match the topic's contract.

Edit `config/structure.json` to declare your own functions and signatures, and
`config/schema.py` if your entry shape differs. This preset mirrors the worked
`example/` project in the okgv repository.

## Instructions

You are generating entries for a synthetic knowledge base using the `okgv` CLI. Run `okgv cli-prompt` to learn how to use the CLI. Do NOT look at okgv source code. Use only the CLI.

## Action

After you understood how to use the CLI ask me what I want to do.
