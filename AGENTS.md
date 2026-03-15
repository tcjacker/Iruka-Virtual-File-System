# Repository Guidelines

## Project Structure & Module Organization

Core runtime code lives in [`iruka_vfs/`](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs). `service.py` is the main entry point; supporting modules handle parsing, path resolution, cache state, SQLAlchemy persistence, and workspace mirroring. Example usage is in [`examples/standalone_sqlite_demo.py`](/Users/tc/ai/Iruka-Virtual-File-System/examples/standalone_sqlite_demo.py). Schema and index SQL lives under [`sql/`](/Users/tc/ai/Iruka-Virtual-File-System/sql). Design notes and benchmark records live in [`docs/`](/Users/tc/ai/Iruka-Virtual-File-System/docs).

## Build, Test, and Development Commands

Set up a local environment with:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Useful commands:

- `python3 examples/standalone_sqlite_demo.py`: runs the standalone SQLite demo end to end.
- `python3 -m compileall iruka_vfs examples/standalone_sqlite_demo.py`: quick syntax and import sanity check.
- `rg 'pattern' iruka_vfs docs examples`: preferred fast search across the repo.

## Coding Style & Naming Conventions

Target Python 3.11+ and use 4-space indentation. Follow existing module style: `snake_case` for files, functions, and variables; `PascalCase` for dataclasses and models; `UPPER_SNAKE_CASE` for constants. Prefer explicit absolute imports such as `from iruka_vfs.service import ...`. Keep modules narrowly scoped and avoid mixing runtime orchestration with persistence or parsing logic.

## Testing Guidelines

There is no dedicated `tests/` suite yet. For any behavioral change, run the demo and `compileall` before submitting. If you add tests, place them under `tests/`, use `test_*.py` naming, and favor `pytest`-style focused unit tests around parsers, path logic, and workspace mutation behavior.

## Commit & Pull Request Guidelines

The visible history currently starts with a single `Initial commit`, so there is no strict legacy convention to preserve. Use short, imperative commit subjects such as `Rename package to iruka_vfs` or `Add workspace mirror lock tests`. Pull requests should include a concise summary, the reason for the change, and exact validation steps you ran. Include sample commands or output when changing public APIs, docs, or runtime behavior.

## Security & Configuration Tips

Do not commit secrets, local database URLs, or generated runtime artifacts. Keep host-specific adapters outside this package unless the change is truly runtime-generic. When documenting connection strings, use placeholders rather than real credentials.
