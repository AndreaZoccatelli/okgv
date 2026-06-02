# Contributing to okgv

Thanks for your interest in contributing.

## Development Setup

```bash
git clone https://github.com/AndreaZoccatelli/okgv.git
cd okgv
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# Unit tests (no external deps)
pytest tests/unit -v

# Integration tests (requires sqlite-vec + embedding model download)
pytest tests/integration -v -m integration

# All tests
pytest -v
```

## Making Changes

1. Fork the repo and create a branch from `main`.
2. Write tests for new functionality.
3. Ensure all tests pass before submitting a PR.
4. Keep commits focused, one logical change per commit.
5. If change impacts agent/user usage update README and templates/prompt. 

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting. CI will reject PRs that fail these checks.

```bash
# Check for lint violations
ruff check .

# Auto-fix what's fixable
ruff check --fix .

# Format code
ruff format .
```

Other conventions:
- Use type hints where practical.
- Keep CLI output as JSON to stdout, logs to stderr.

## Reporting Bugs

Open an issue at https://github.com/AndreaZoccatelli/okgv/issues with:
- What you did
- What you expected
- What happened instead
- Python version and OS

## Pull Requests

- Keep PRs small and focused.
- Reference related issues in the PR description.
- All CI checks must pass.
