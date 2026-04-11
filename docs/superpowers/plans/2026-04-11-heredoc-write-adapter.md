# Heredoc Write Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bash-looking heredoc write protocol that safely writes large text bodies into the VFS without routing the body through the existing command parser.

**Architecture:** Detect a narrow `cat > PATH <<'EOF' ... EOF` / `cat >> PATH <<'EOF' ... EOF` shape before the normal command chain runs. Convert a matched heredoc block into a structured VFS write operation, return command-style results and artifacts, and reject malformed heredoc-looking input with explicit parse errors.

**Tech Stack:** Python 3.11+, unittest, existing VFS service/runtime helpers

---

### Task 1: Add failing heredoc adapter tests

**Files:**
- Create: `tests/test_heredoc_adapter.py`
- Test: `tests/test_heredoc_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_single_quoted_heredoc_write():
    parsed = parse_heredoc_write_command(
        "cat > /workspace/site/index.html <<'EOF'\n<html>\n  <div>$NOT_EXPANDED</div>\n</html>\nEOF"
    )
    assert parsed is not None
    assert parsed.mode == "write"
    assert parsed.path == "/workspace/site/index.html"
    assert parsed.content == "<html>\n  <div>$NOT_EXPANDED</div>\n</html>\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_heredoc_adapter`
Expected: FAIL because the parser helper does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class HeredocWriteCommand:
    mode: str
    path: str
    content: str
    delimiter: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_heredoc_adapter`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_heredoc_adapter.py iruka_vfs/heredoc_adapter.py
git commit -m "Add heredoc write adapter parser"
```

### Task 2: Add failing integration tests for heredoc execution

**Files:**
- Modify: `tests/test_service_ops_file_api.py`
- Test: `tests/test_service_ops_file_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_run_virtual_bash_executes_heredoc_write():
    raw_cmd = "cat > /workspace/site/index.html <<'EOF'\n<html>\nhello\n</html>\nEOF"
    payload = run_virtual_bash(...)
    assert payload["exit_code"] == 0
    assert payload["artifacts"]["protocol"] == "heredoc_write"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_service_ops_file_api`
Expected: FAIL because heredoc input still falls into the normal bash parser

- [ ] **Step 3: Write minimal implementation**

```python
parsed = parse_heredoc_write_command(raw_cmd)
if parsed:
    result = run_heredoc_write_command(db, session, parsed)
else:
    result = run_command_chain(db, session, raw_cmd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_service_ops_file_api`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_service_ops_file_api.py iruka_vfs/service_ops/file_api.py iruka_vfs/heredoc_adapter.py
git commit -m "Execute heredoc file writes in virtual bash"
```

### Task 3: Reject malformed heredoc-looking input with explicit errors

**Files:**
- Modify: `tests/test_heredoc_adapter.py`
- Modify: `tests/test_service_ops_file_api.py`
- Modify: `iruka_vfs/heredoc_adapter.py`
- Modify: `iruka_vfs/service_ops/file_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_heredoc_write_reports_missing_terminator():
    with self.assertRaisesRegex(ValueError, "missing heredoc terminator"):
        parse_heredoc_write_command("cat > /workspace/a.txt <<'EOF'\nhello\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_heredoc_adapter tests.test_service_ops_file_api`
Expected: FAIL because malformed heredoc input is not distinguished from generic bash

- [ ] **Step 3: Write minimal implementation**

```python
if looks_like_heredoc_write(raw_cmd) and parse_error:
    return VirtualCommandResult("", parse_error, 2, {"protocol": "heredoc_write"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_heredoc_adapter tests.test_service_ops_file_api`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_heredoc_adapter.py tests/test_service_ops_file_api.py iruka_vfs/heredoc_adapter.py iruka_vfs/service_ops/file_api.py
git commit -m "Validate heredoc write protocol errors"
```

### Task 4: Verify narrow compatibility and syntax safety

**Files:**
- Modify: `tests/test_runtime_executor.py`
- Test: `tests/test_runtime_executor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_non_heredoc_commands_still_use_existing_parser():
    result = run_single_command(None, session, "echo hello")
    assert result.exit_code == 0
    assert result.stdout == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_runtime_executor`
Expected: FAIL only if the heredoc integration accidentally breaks ordinary command execution

- [ ] **Step 3: Write minimal implementation**

```python
# No new code if green already holds; keep this as an explicit regression check.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_runtime_executor tests.test_heredoc_adapter tests.test_service_ops_file_api`
Expected: PASS for the relevant regression checks

- [ ] **Step 5: Commit**

```bash
git add tests/test_runtime_executor.py
git commit -m "Cover heredoc adapter regression checks"
```
