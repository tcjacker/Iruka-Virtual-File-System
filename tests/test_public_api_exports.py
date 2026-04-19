from __future__ import annotations

import importlib
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

import iruka_vfs
from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


class PublicApiExportsTest(unittest.TestCase):
    def test_top_level_all_only_lists_recommended_exports(self) -> None:
        self.assertEqual(
            iruka_vfs.__all__,
            [
                "ExternalFileSource",
                "VFSDependencies",
                "VirtualWorkspace",
                "WritableFileSource",
                "configure_vfs_dependencies",
                "create_workspace",
            ],
        )

    def test_compatibility_factory_alias_warns_when_called(self) -> None:
        workspace = SimpleNamespace(id=7, tenant_id="tenant-a", runtime_key="runtime-a")
        with patch("iruka_vfs.workspace._create_workspace_handle", return_value="workspace-handle") as factory:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                result = iruka_vfs.create_workspace_handle(workspace=workspace)

        self.assertEqual(result, "workspace-handle")
        self.assertTrue(any(item.category is DeprecationWarning for item in captured))
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["workspace"], workspace)

    def test_compatibility_handle_alias_warns_on_package_access_and_preserves_identity(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            workspace_handle_type = iruka_vfs.VirtualWorkspaceHandle

        self.assertIs(workspace_handle_type, VirtualWorkspace)
        self.assertTrue(any(item.category is DeprecationWarning for item in captured))

    def test_deprecated_workspace_methods_warn_and_forward(self) -> None:
        workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )
        cases = [
            ("bash", "run", (object(), "pwd"), {}, {"exit_code": 0}),
            ("write_file", "write", (object(), "/workspace/a.txt", "hello"), {}, {"version": 1}),
            ("tool_write", "write", (object(), "/workspace/a.txt", "hello"), {}, {"version": 2}),
            (
                "tool_edit",
                "edit",
                (object(), "/workspace/a.txt", "before", "after"),
                {"replace_all": True},
                {"version": 3},
            ),
            ("enter_agent_mode", "_enter_agent_mode", (object(),), {"flush": False}, "agent"),
            ("enter_host_mode", "_enter_host_mode", (object(),), {"flush": False}, "host"),
            ("access_mode", "_access_mode", (object(),), {}, "host"),
            ("tree", "_tree", (object(),), {}, "/workspace"),
        ]

        for deprecated_name, forwarded_name, args, kwargs, expected in cases:
            with self.subTest(method=deprecated_name):
                with patch.object(VirtualWorkspace, forwarded_name, return_value=expected) as forwarded:
                    with warnings.catch_warnings(record=True) as captured:
                        warnings.simplefilter("always")
                        result = getattr(workspace, deprecated_name)(*args, **kwargs)

                self.assertEqual(result, expected)
                self.assertTrue(any(item.category is DeprecationWarning for item in captured))
                forwarded.assert_called_once_with(*args, **kwargs)

    def test_service_deprecated_entry_warns_when_called(self) -> None:
        module = importlib.import_module("iruka_vfs.service")
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with patch.object(module, "_run_virtual_bash", return_value={"exit_code": 0}) as run_virtual_bash:
                result = module.run_virtual_bash(object(), object(), "pwd", runtime_seed=SimpleNamespace(), tenant_id="tenant-a")

        self.assertEqual(result, {"exit_code": 0})
        self.assertTrue(any(item.category is DeprecationWarning for item in captured))
        run_virtual_bash.assert_called_once()

    def test_service_diagnostic_function_does_not_warn(self) -> None:
        module = importlib.import_module("iruka_vfs.service")
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with patch.object(module, "snapshot_virtual_fs_cache_metrics", return_value={"hits": 1}):
                module.snapshot_virtual_fs_cache_metrics()
        self.assertEqual(captured, [])

    def test_service_private_helper_bindings_remain_available_for_internal_importers(self) -> None:
        module = importlib.import_module("iruka_vfs.service")

        from iruka_vfs.memory_cache import get_node_content
        from iruka_vfs.pathing import resolve_path
        from iruka_vfs.runtime import get_or_create_root, must_get_node

        self.assertIs(module._get_node_content, get_node_content)
        self.assertIs(module._resolve_path, resolve_path)
        self.assertIs(module._get_or_create_root, get_or_create_root)
        self.assertIs(module._must_get_node, must_get_node)
