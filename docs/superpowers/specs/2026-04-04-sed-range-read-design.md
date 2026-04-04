# Sed Range Read Design

## Goal

Add bash-aligned segmented file reading to the virtual runtime by supporting the minimal `sed` form:

```bash
sed -n 'START,ENDp' FILE
```

The feature is limited to line-range reads from a single virtual file. It does not attempt to implement general-purpose `sed`.

## Scope

In scope:

- Add `sed` to the supported virtual commands.
- Accept exactly `sed -n 'START,ENDp' FILE`.
- Read a single virtual file and print the inclusive line range from `START` to `END`.
- Return clear user-facing errors for malformed usage and missing files.
- Add focused automated tests for the supported form and error cases.

Out of scope:

- Multiple files.
- Reading from stdin.
- Other `sed` flags or expressions.
- Multiple commands or chained `sed` programs.
- Address syntaxes other than `START,ENDp`.

## User-Facing Behavior

The command behavior should match common shell expectations for the supported subset:

- `START` and `END` are 1-based inclusive line numbers.
- If `END` exceeds the file length, output stops at the end of file.
- If the selected range is valid but produces no lines because the file is empty, output is empty with exit code `0`.
- If the file does not exist or is not a file, return a `sed: ... No such file` style error and exit code `1`.
- If the syntax is unsupported, return a `sed:` error explaining the accepted form and exit code `1`.

## Parsing Rules

The runtime already tokenizes shell-like argv values before dispatch. The new `sed` handling will:

- Require argv layout equivalent to `["sed", "-n", "START,ENDp", "FILE"]`.
- Reject any invocation with missing `-n`, extra positional arguments, or extra flags.
- Parse the expression using a strict regex for `^([0-9]+),([0-9]+)p$`.
- Reject `START < 1`.
- Reject `END < START`.

This keeps the implementation small and avoids ambiguous partial support.

## Runtime Design

Implementation will live in the virtual command executor layer, alongside other built-in commands.

- Add `sed` to `SUPPORTED_COMMANDS`.
- Add a small helper in the runtime command module to parse and execute the supported `sed` subset.
- Resolve the file through the existing workspace path resolution utilities.
- Read full file content with the existing content loader, then split into lines while preserving original line endings semantics as closely as practical for the current runtime conventions.
- Slice the requested inclusive line range and join it into stdout.

The host-side `read_workspace_file(...)` and SDK `read_file(...)` APIs will remain unchanged. This feature is a shell command capability, not a host API pagination primitive.

## Testing Plan

Tests will be added before implementation and will cover:

- Reading a middle range from a multi-line file.
- Reading a single line with `START == END`.
- Reading a range whose end exceeds file length.
- Rejecting malformed expressions.
- Rejecting missing files.

The repository currently has minimal test coverage, so these tests should be focused and self-contained.

## Risks And Constraints

- This is intentionally not real `sed`; error messages must make the supported subset obvious.
- Line splitting must avoid off-by-one behavior around trailing newlines.
- The new command should not change existing parser behavior for other commands.

## Acceptance Criteria

- `sed -n '2,4p' /workspace/file.txt` returns exactly lines 2 through 4.
- Invalid expressions fail deterministically with exit code `1`.
- Existing virtual commands continue to work unchanged.
- New tests fail before implementation and pass after implementation.
