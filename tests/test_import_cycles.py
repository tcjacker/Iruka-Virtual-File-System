from __future__ import annotations

import importlib
import unittest


class ImportCycleTest(unittest.TestCase):
    def test_core_service_import_chain_has_no_cycle(self) -> None:
        modules = [
            "iruka_vfs.service",
            "iruka_vfs.integrations.agent.access_mode",
            "iruka_vfs.service_ops.bootstrap",
            "iruka_vfs.workspace_mirror",
            "iruka_vfs.mirror.checkpoint",
            "iruka_vfs.service_ops",
            "iruka_vfs.integrations.agent",
        ]
        for name in modules:
            with self.subTest(module=name):
                module = importlib.import_module(name)
                self.assertIsNotNone(module)
