"""CLI helpers: output, errors, input parsing, logging."""

import json
import os
import sys
from typing import NoReturn

import click

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONNECTION = 4


def env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def output(data: dict | list) -> None:
    """Write JSON to stdout."""
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def err(error: str, detail: str = "", suggestion: str = "", exit_code: int = EXIT_FAILURE) -> NoReturn:
    """Write structured error to stderr and exit."""
    msg: dict = {"error": error}
    if detail:
        msg["detail"] = detail
    if suggestion:
        msg["suggestion"] = suggestion
    json.dump(msg, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(exit_code)


def parse_raw(raw_str: str) -> dict:
    """Parse JSON string into dict."""
    try:
        return json.loads(raw_str)
    except json.JSONDecodeError as e:
        err("invalid_json", detail=str(e), exit_code=EXIT_USAGE)


def read_raw(entry_str: str) -> dict:
    """Read raw dict from argument or stdin (if '-')."""
    if entry_str == "-":
        return parse_raw(sys.stdin.read())
    return parse_raw(entry_str)


def log(msg: str) -> None:
    """Progress/info to stderr."""
    click.echo(msg, err=True)
