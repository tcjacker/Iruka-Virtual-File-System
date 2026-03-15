from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExternalFileSource:
    file_id: str
    virtual_path: str
    read_text: Callable[[], str]
    metadata: dict[str, Any] = field(default_factory=dict)
    writable: bool = False
    version_token: str | None = None


@dataclass(frozen=True)
class WritableFileSource(ExternalFileSource):
    write_text: Callable[[str], None] | None = None
    writable: bool = True

    def __post_init__(self) -> None:
        if self.write_text is None:
            raise ValueError("WritableFileSource requires write_text")
