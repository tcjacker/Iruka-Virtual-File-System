# Host Adapter Contract

`iruka_vfs` owns the VFS runtime.

The host service owns:

- conversations and requests
- chapters, documents, or other source records
- project or domain state
- runtime selection for one agent execution

The host adapter translates host concepts into VFS concepts.

## Responsibilities

The host adapter should:

1. Resolve host context such as `tenant_id`, `runtime_key`, and source record identifiers
2. Build one workspace object for one agent
3. Map one writable host file to the workspace's `primary_file`
4. Map readonly host context and skill data to `context_files` and `skill_files`
5. Call `workspace.ensure(db)` before command execution
6. Call `workspace.bash(db, "...")` for each virtual command
7. Call `workspace.flush()` at turn end or another explicit durability boundary

The host adapter should not push host-only business models into VFS APIs.

## Recommended API

```python
from iruka_vfs import WritableFileSource, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id=str(workspace_model.tenant_id),
    runtime_key=str(workspace_model.runtime_key),
    chapter_id=chapter.id,
    primary_file=WritableFileSource(
        file_id=f"chapter:{chapter.id}",
        virtual_path=f"/workspace/chapters/chapter_{chapter.id}.md",
        read_text=lambda: chapter.body_text,
        write_text=lambda text: save_chapter_body(chapter.id, text),
    ),
    context_files={"outline.md": outline_text},
    skill_files={"style.md": style_text},
)

workspace.ensure(db)
result = workspace.bash(db, "edit /workspace/chapters/chapter_123.md --find foo --replace bar")
workspace.flush()
```

`RuntimeSeed` still exists internally, but the preferred host-facing API is the workspace object.

## Lifecycle Rules

The host should model one workspace as one agent execution context. A workspace may survive across multiple agent turns, but command execution on that workspace should stay serialized.

Required constraints:

- do not run concurrent commands on the same workspace
- do not share one live SQLAlchemy `Session` across requests or threads
- prefer storing only workspace identifiers and source bindings in a reusable adapter object
- bind the current request's DB session when calling `workspace.ensure(db)` and `workspace.bash(db, "...")`
- keep `workspace.flush()` as an explicit end-of-turn durability action

This keeps Redis-backed workspace state reusable while avoiding stale DB sessions and request-crossing runtime objects.

## Minimal Mapping

Typical chapter-based host mapping:

- host conversation/request -> choose runtime/workspace
- host chapter/document -> one writable VFS file like `/workspace/chapters/chapter_123.md`
- host project state -> `/workspace/context/*.md`
- host skills -> `/workspace/skills/*.md`

## Required Handle Inputs

At minimum the adapter must provide:

- `workspace`
- `runtime_key`
- `tenant_id`
- `primary_file`

`primary_file` should usually be a `WritableFileSource` with:

- `virtual_path`
- `read_text()`
- `write_text(text)`

## Reference Pattern

Keep host-specific seed builders outside this repository. A host integration layer should be the only place that knows how to turn a chapter, document, or project record into VFS file sources.
