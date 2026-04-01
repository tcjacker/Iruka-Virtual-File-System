# Contributing

Thanks for contributing to `iruka_vfs`.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .[dev]
```

## Validation

Run these checks before opening a pull request:

```bash
python3 -m pytest
python3 -m compileall iruka_vfs examples/standalone_sqlite_demo.py
```

If you change persistence or runtime profile behavior, also run the smallest relevant reproduction in `examples/` or the targeted test module under `tests/`.

## Pull Requests

- Keep changes scoped and explain the motivation.
- Add or update tests when behavior changes.
- Document user-facing API or runtime-semantics changes in `README.md`, `README.zh-CN.md`, or `docs/` as appropriate.
- Do not commit secrets, local database URLs, or generated artifacts.

## Style

- Target Python 3.11+.
- Use 4-space indentation.
- Prefer explicit imports from `iruka_vfs`.
- Keep runtime orchestration, persistence, and parsing concerns separated.
