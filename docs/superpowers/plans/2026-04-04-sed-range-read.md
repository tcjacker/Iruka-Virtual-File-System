# Sed Range Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add minimal bash-aligned segmented file reading by supporting `sed -n 'START,ENDp' FILE` in the virtual runtime.

**Architecture:** Extend the runtime command dispatcher with a tightly scoped `sed` handler that accepts only the approved subset, resolves one virtual file, slices the requested inclusive line range, and returns stdout or a deterministic error. Keep host APIs unchanged and cover behavior with focused executor tests.

**Tech Stack:** Python 3.11+, unittest, existing `iruka_vfs.runtime.executor` command dispatch

---

### Task 1: Add Failing Tests For Supported Sed Range Reads

**Files:**
- Create: `tests/test_runtime_executor.py`
- Modify: `iruka_vfs/runtime/executor.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_exec_argv_sed_prints_inclusive_line_range(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        node = SimpleNamespace(id=11, node_type="file")
        with patch.object(service, "_resolve_path", return_value=node), patch.object(
            service,
            "_get_node_content",
            return_value="line1\nline2\nline3\nline4\n",
        ):
            result = exec_argv(None, session, ["sed", "-n", "2,3p", "notes.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "line2\nline3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_runtime_executor.RuntimeExecutorTest.test_exec_argv_sed_prints_inclusive_line_range -v`
Expected: FAIL because `sed` is still unsupported.

- [ ] **Step 3: Write minimal implementation**

```python
SUPPORTED_COMMANDS = {
    # ...
    "sed",
}
```

Add a dedicated helper for the `sed -n START,ENDp FILE` subset and call it from `exec_argv(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_runtime_executor.RuntimeExecutorTest.test_exec_argv_sed_prints_inclusive_line_range -v`
Expected: PASS.

### Task 2: Add Boundary And Error Tests

**Files:**
- Modify: `tests/test_runtime_executor.py`
- Modify: `iruka_vfs/runtime/executor.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_exec_argv_sed_clamps_end_line_to_file_length(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        node = SimpleNamespace(id=11, node_type="file")
        with patch.object(service, "_resolve_path", return_value=node), patch.object(
            service,
            "_get_node_content",
            return_value="line1\nline2\nline3\n",
        ):
            result = exec_argv(None, session, ["sed", "-n", "2,9p", "notes.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "line2\nline3")

    def test_exec_argv_sed_rejects_invalid_expression(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        result = exec_argv(None, session, ["sed", "-n", "3p", "notes.txt"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("supported form", result.stderr)

    def test_exec_argv_sed_rejects_missing_file(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        with patch.object(service, "_resolve_path", return_value=None):
            result = exec_argv(None, session, ["sed", "-n", "1,2p", "missing.txt"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "sed: missing.txt: No such file")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_runtime_executor.RuntimeExecutorTest -v`
Expected: FAIL on the new scenarios until parsing and slicing behavior are complete.

- [ ] **Step 3: Write minimal implementation**

```python
match = re.fullmatch(r"([0-9]+),([0-9]+)p", expression)
if not match:
    return VirtualCommandResult("", "sed: unsupported expression; supported form: sed -n 'START,ENDp' FILE", 1, {})
```

Implement:
- strict argv validation
- `START >= 1`
- `END >= START`
- end-line clamping
- missing-file error

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_runtime_executor.RuntimeExecutorTest -v`
Expected: PASS.

### Task 3: Verify Full Repository Checks For This Change

**Files:**
- Modify: `docs/superpowers/specs/2026-04-04-sed-range-read-design.md`
- Modify: `docs/superpowers/plans/2026-04-04-sed-range-read.md`
- Modify: `tests/test_runtime_executor.py`
- Modify: `iruka_vfs/runtime/executor.py`

- [ ] **Step 1: Run focused automated tests**

Run: `python3 -m unittest tests.test_runtime_executor tests.test_command_parser tests.test_runtime_helpers -v`
Expected: PASS.

- [ ] **Step 2: Run syntax validation**

Run: `python3 -m compileall iruka_vfs tests`
Expected: PASS with no syntax errors.

- [ ] **Step 3: Commit the change**

```bash
git add docs/superpowers/specs/2026-04-04-sed-range-read-design.md docs/superpowers/plans/2026-04-04-sed-range-read.md tests/test_runtime_executor.py iruka_vfs/runtime/executor.py
git commit -m "Add sed line range reads"
```
