from __future__ import annotations

import os
import re


REGEX_META_CHARS = re.compile(r"[.\^\$\*\+\?\{\}\[\]\|\\\(\)]")
ASYNC_COMMAND_LOGGING = os.getenv("VFS_ASYNC_COMMAND_LOGGING", "1") != "0"
MEMORY_CACHE_ENABLED = os.getenv("VFS_MEMORY_CACHE_ENABLED", "1") != "0"
MEMORY_CACHE_MAX_BYTES = int(os.getenv("VFS_MEMORY_CACHE_MAX_BYTES", str(32 * 1024 * 1024)))
MEMORY_CACHE_MAX_FILES = int(os.getenv("VFS_MEMORY_CACHE_MAX_FILES", "300"))
MEMORY_CACHE_FLUSH_INTERVAL_SECONDS = float(os.getenv("VFS_MEMORY_CACHE_FLUSH_INTERVAL_SECONDS", "0.25"))
MEMORY_CACHE_FLUSH_BATCH = int(os.getenv("VFS_MEMORY_CACHE_FLUSH_BATCH", "64"))
VFS_COMMAND_LOG_MAX_STDOUT_CHARS = int(os.getenv("VFS_COMMAND_LOG_MAX_STDOUT_CHARS", "8000"))
VFS_COMMAND_LOG_MAX_STDERR_CHARS = int(os.getenv("VFS_COMMAND_LOG_MAX_STDERR_CHARS", "4000"))
VFS_ROOT = "/workspace"
VFS_CHAPTERS_ROOT = "/workspace/chapters"
VFS_NOTES_ROOT = "/workspace/notes"
VFS_CONTEXT_ROOT = "/workspace/context"
VFS_SKILLS_ROOT = "/workspace/skills"
