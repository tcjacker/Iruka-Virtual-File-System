# Structured Tool Write/Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Code-style structured `tool_write` and `tool_edit` APIs to the VFS workspace facade, backed by the existing host-side file write and text edit kernels.

**Architecture:** Keep structured tools on the host-side API path rather than routing them through `bash`. `tool_write` wraps the existing file-seeding/write path with structured metadata, while `tool_edit` reuses the existing text replacement kernel and write primitive to provide deterministic single-match edits with explicit replacement counts.

**Tech Stack:** Python 3.11+, unittest, existing VFS service/service_ops/runtime helpers

---

### Task 1: Add failing tests for structured tool operations

**Files:**
- Create: `tests/test_service_ops_structured_tools.py`
- Test: `tests/test_service_ops_structured_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_tool_write_workspace_file_returns_structured_result():
    payload = tool_write_workspace_file(...)
    assert payload["operation"] == "tool_write"
    assert payload["bytes_written"] == len("hello")

def test_tool_edit_workspace_file_returns_structured_result():
    payload = tool_edit_workspace_file(...)
    assert payload["operation"] == "tool_edit"
    assert payload["replacements"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_service_ops_structured_tools`
Expected: FAIL because the structured tool APIs do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
def tool_write_workspace_file(...):
    ...

def tool_edit_workspace_file(...):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_service_ops_structured_tools`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_service_ops_structured_tools.py iruka_vfs/service_ops/file_api.py
git commit -m "Add structured file tool APIs"
```

### Task 2: Expose structured tools through service and workspace facade

**Files:**
- Create: `tests/test_workspace_handle_tools.py`
- Modify: `iruka_vfs/service.py`
- Modify: `iruka_vfs/sdk/workspace_handle.py`
- Test: `tests/test_workspace_handle_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_workspace_tool_write_delegates_to_service():
    result = workspace.tool_write(db, "/workspace/a.txt", "hello")
    assert result["operation"] == "tool_write"

def test_workspace_tool_edit_delegates_to_service():
    result = workspace.tool_edit(db, "/workspace/a.txt", "a", "b")
    assert result["operation"] == "tool_edit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_workspace_handle_tools`
Expected: FAIL because the workspace facade does not expose the methods yet

- [ ] **Step 3: Write minimal implementation**

```python
def tool_write(self, db, path, content):
    return service.tool_write_workspace_file(...)

def tool_edit(self, db, path, old_text, new_text, *, replace_all=False):
    return service.tool_edit_workspace_file(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_workspace_handle_tools`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_workspace_handle_tools.py iruka_vfs/service.py iruka_vfs/sdk/workspace_handle.py
git commit -m "Expose structured write and edit tools"
```

### Task 3: Update README guidance for structured tools

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Update API and usage docs**

```markdown
- `workspace.tool_write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text, replace_all=False)`
```

- [ ] **Step 2: Run syntax/docs verification**

Run: `PYTHONPATH=. ./.venv/bin/python -m unittest tests.test_service_ops_structured_tools tests.test_workspace_handle_tools`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-CN.md
git commit -m "Document structured write and edit tools"
```
