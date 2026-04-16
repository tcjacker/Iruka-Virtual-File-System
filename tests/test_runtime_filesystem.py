from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs import service
from iruka_vfs.runtime.filesystem import write_file


class RuntimeFilesystemTest(unittest.TestCase):
    def test_write_file_does_not_depend_on_service_memory_cache_enabled(self) -> None:
        node = SimpleNamespace(
            id=11,
            workspace_id=7,
            tenant_id="tenant-a",
            version_no=3,
            content_text="before",
            updated_at=None,
        )

        had_attr = hasattr(service, "MEMORY_CACHE_ENABLED")
        original = getattr(service, "MEMORY_CACHE_ENABLED", None)
        if had_attr:
            delattr(service, "MEMORY_CACHE_ENABLED")

        try:
            with patch("iruka_vfs.runtime.filesystem.get_workspace_mirror", return_value=None), patch(
                "iruka_vfs.runtime.filesystem._update_cache_after_write", return_value=4
            ) as update_cache:
                version = write_file(object(), node, "after", op="unit_test")
        finally:
            if had_attr:
                setattr(service, "MEMORY_CACHE_ENABLED", original)

        self.assertEqual(version, 4)
        update_cache.assert_called_once_with(node, "after", op="unit_test")


if __name__ == "__main__":
    unittest.main()
