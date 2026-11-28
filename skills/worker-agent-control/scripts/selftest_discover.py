"""Test worker routing without calling a model or modifying a worker workspace."""

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

SPEC = importlib.util.spec_from_file_location(
    "worker_discover", pathlib.Path(__file__).with_name("discover.py")
)
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


class WorkerRoutingTests(unittest.TestCase):
    """Exercise named selection, legacy compatibility, and pre-execution rejection."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="worker-routing.")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.config_path = self.root / "config.json"
        self.legacy = {
            "workspace": str(self.root / "legacy"),
            "default_provider": "claude",
            "discovery_timeout_seconds": 30,
            "claude_discovery_max_turns": 8,
            "context_files": ["CLAUDE.md"],
        }
        self.registry = {
            "discovery_timeout_seconds": 30,
            "claude_discovery_max_turns": 8,
            "workers": {
                "builder": {
                    "workspace": str(self.root / "builder"),
                    "default_provider": "claude",
                    "context_files": ["CLAUDE.md"],
                },
                "reporter": {
                    "workspace": str(self.root / "reporter"),
                    "default_provider": "codex",
                    "context_files": ["AGENTS.md"],
                    "discovery_timeout_seconds": 60,
                },
            },
        }

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def test_each_worker_keeps_its_provider_and_context(self):
        self.write_config(self.registry)
        for name in ("builder", "reporter"):
            with self.subTest(worker=name):
                actual = DISCOVERY.load_worker_config(self.config_path, name)
                expected = self.registry["workers"][name]
                for key in ("workspace", "default_provider", "context_files"):
                    self.assertEqual(actual[key], expected[key])  # noqa: PT009 - stdlib unittest runner.
        self.assertEqual(actual["discovery_timeout_seconds"], 60)  # noqa: PT009 - stdlib unittest runner.

    def test_legacy_config_is_preserved(self):
        self.write_config(self.legacy)
        self.assertEqual(  # noqa: PT009 - stdlib unittest runner.
            DISCOVERY.load_worker_config(self.config_path, None), self.legacy
        )

    def test_legacy_cli_overrides_remain_available(self):
        self.write_config(self.legacy)
        workspace = self.root / "explicit"
        argv = ["discover.py", "--config", str(self.config_path),
                "--workspace", str(workspace), "--provider", "codex",
                "--output-dir", str(self.root / "attempt")]
        with unittest.mock.patch.object(sys, "argv", argv):
            args, _ = DISCOVERY.parse_arguments()
        self.assertEqual((args.workspace, args.provider), (workspace, "codex"))  # noqa: PT009 - stdlib unittest runner.

    def test_named_cli_selects_native_provider(self):
        self.write_config(self.registry)
        argv = ["discover.py", "--config", str(self.config_path),
                "--worker", "reporter", "--output-dir", str(self.root / "attempt")]
        with unittest.mock.patch.object(sys, "argv", argv):
            args, _ = DISCOVERY.parse_arguments()
        self.assertEqual(args.provider, "codex")  # noqa: PT009 - stdlib unittest runner.
        self.assertEqual(args.timeout, 60)  # noqa: PT009 - stdlib unittest runner.

    def test_registry_requires_explicit_worker(self):
        self.write_config(self.registry)
        with self.assertRaisesRegex(DISCOVERY.DiscoveryError, "WORKER_REQUIRED"):  # noqa: PT027 - stdlib unittest runner.
            DISCOVERY.load_worker_config(self.config_path, None)

    def test_unknown_worker_is_not_replaced(self):
        self.write_config(self.registry)
        with self.assertRaisesRegex(DISCOVERY.DiscoveryError, "UNKNOWN_WORKER"):  # noqa: PT027 - stdlib unittest runner.
            DISCOVERY.load_worker_config(self.config_path, "missing")

    def test_legacy_config_rejects_worker_name(self):
        self.write_config(self.legacy)
        with self.assertRaisesRegex(DISCOVERY.DiscoveryError, "WORKER_NOT_CONFIGURED"):  # noqa: PT027 - stdlib unittest runner.
            DISCOVERY.load_worker_config(self.config_path, "builder")

    def test_invalid_context_and_budgets_are_rejected(self):
        for key, value in (
            ("context_files", []), ("context_files", [1]),
            ("discovery_timeout_seconds", 0), ("claude_discovery_max_turns", True),
            ("default_provider", "other"),
        ):
            with self.subTest(key=key, value=value):
                self.write_config({**self.legacy, key: value})
                with self.assertRaises(DISCOVERY.DiscoveryError):  # noqa: PT027 - stdlib unittest runner.
                    DISCOVERY.load_worker_config(self.config_path, None)

    def reject_before_native(self, arguments, message):
        self.write_config(self.registry)
        output = self.root / "attempt"
        argv = ["discover.py", "--config", str(self.config_path),
                "--output-dir", str(output), *arguments]
        errors = io.StringIO()
        with (
            unittest.mock.patch.object(sys, "argv", argv),
            unittest.mock.patch.object(DISCOVERY, "run_native") as native,
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,  # noqa: PT027 - stdlib unittest runner.
        ):
            DISCOVERY.main()
        self.assertEqual(raised.exception.code, 2)  # noqa: PT009 - stdlib unittest runner.
        self.assertIn(message, errors.getvalue())  # noqa: PT009 - stdlib unittest runner.
        self.assertFalse(output.exists())  # noqa: PT009 - stdlib unittest runner.
        native.assert_not_called()

    def test_claude_worker_cannot_be_rerouted_to_codex(self):
        self.reject_before_native(
            ["--worker", "builder", "--provider", "codex"],
            "WORKER_PROVIDER_OVERRIDE_REJECTED",
        )

    def test_codex_worker_cannot_be_rerouted_to_claude(self):
        self.reject_before_native(
            ["--worker", "reporter", "--provider", "claude"],
            "WORKER_PROVIDER_OVERRIDE_REJECTED",
        )

    def test_worker_workspace_cannot_be_replaced(self):
        self.reject_before_native(
            ["--worker", "builder", "--workspace", str(self.root / "other")],
            "WORKER_WORKSPACE_OVERRIDE_REJECTED",
        )

    def test_missing_worker_is_rejected_before_native(self):
        self.reject_before_native([], "WORKER_REQUIRED")

    def test_unknown_worker_is_rejected_before_native(self):
        self.reject_before_native(["--worker", "missing"], "UNKNOWN_WORKER")

    def test_registry_without_top_level_provider_is_not_a_key_error(self):
        # The 1.5.0 regression: a workers-only config raised KeyError('default_provider').
        self.assertNotIn("default_provider", self.registry)  # noqa: PT009 - stdlib unittest runner.
        self.reject_before_native([], "WORKER_REQUIRED:builder,reporter")

    def test_missing_explicit_config_is_not_replaced_by_example(self):
        self.config_path = self.root / "absent.json"
        output = self.root / "attempt"
        argv = ["discover.py", "--config", str(self.config_path), "--output-dir", str(output)]
        errors = io.StringIO()
        with (
            unittest.mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,  # noqa: PT027 - stdlib unittest runner.
        ):
            DISCOVERY.parse_arguments()
        self.assertEqual(raised.exception.code, 2)  # noqa: PT009 - stdlib unittest runner.
        self.assertIn("absent.json", errors.getvalue())  # noqa: PT009 - stdlib unittest runner.
        self.assertFalse(output.exists())  # noqa: PT009 - stdlib unittest runner.

    def test_default_config_falls_back_to_example_and_refuses_without_workspace(self):
        skill = self.root / "skill"
        skill.mkdir()
        (skill / "config.example.json").write_text(json.dumps({**self.legacy, "workspace": None}), encoding="utf-8")
        output = self.root / "attempt"
        errors = io.StringIO()
        with (
            unittest.mock.patch.object(DISCOVERY, "SKILL", skill),
            unittest.mock.patch.object(sys, "argv", ["discover.py", "--output-dir", str(output)]),
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit) as raised,  # noqa: PT027 - stdlib unittest runner.
        ):
            DISCOVERY.parse_arguments()
        self.assertEqual(raised.exception.code, 2)  # noqa: PT009 - stdlib unittest runner.
        self.assertIn("no worker is connected", errors.getvalue())  # noqa: PT009 - stdlib unittest runner.
        self.assertFalse(output.exists())  # noqa: PT009 - stdlib unittest runner.

    def test_default_config_prefers_local_file(self):
        skill = self.root / "skill"
        skill.mkdir()
        (skill / "config.example.json").write_text("{}", encoding="utf-8")
        (skill / "config.json").write_text("{}", encoding="utf-8")
        with unittest.mock.patch.object(DISCOVERY, "SKILL", skill):
            self.assertEqual(DISCOVERY.default_config_path(), skill / "config.json")  # noqa: PT009 - stdlib unittest runner.

    def test_null_workspace_accepts_explicit_workspace(self):
        self.write_config({**self.legacy, "workspace": None})
        workspace = self.root / "explicit"
        argv = ["discover.py", "--config", str(self.config_path), "--workspace", str(workspace),
                "--output-dir", str(self.root / "attempt")]
        with unittest.mock.patch.object(sys, "argv", argv):
            args, _ = DISCOVERY.parse_arguments()
        self.assertEqual(args.workspace, workspace.resolve())  # noqa: PT009 - stdlib unittest runner.
        self.assertEqual(args.config, self.config_path.resolve())  # noqa: PT009 - stdlib unittest runner.

    def test_max_turns_is_required_only_for_claude(self):
        flat = {key: value for key, value in self.legacy.items() if key != "claude_discovery_max_turns"}
        self.write_config(flat)
        base = ["discover.py", "--config", str(self.config_path), "--output-dir", str(self.root / "attempt")]
        with unittest.mock.patch.object(sys, "argv", [*base, "--provider", "codex"]):
            args, _ = DISCOVERY.parse_arguments()
        self.assertEqual(args.provider, "codex")  # noqa: PT009 - stdlib unittest runner.
        errors = io.StringIO()
        with (
            unittest.mock.patch.object(sys, "argv", base),
            contextlib.redirect_stderr(errors),
            self.assertRaises(SystemExit),  # noqa: PT027 - stdlib unittest runner.
        ):
            DISCOVERY.parse_arguments()
        self.assertIn("claude_discovery_max_turns", errors.getvalue())  # noqa: PT009 - stdlib unittest runner.

    def test_named_worker_workspace_must_be_present(self):
        self.registry["workers"]["builder"]["workspace"] = ""
        self.write_config(self.registry)
        with self.assertRaisesRegex(DISCOVERY.DiscoveryError, "CONFIG_WORKSPACE_MISSING:builder"):  # noqa: PT027 - stdlib unittest runner.
            DISCOVERY.load_worker_config(self.config_path, "builder")


if __name__ == "__main__":
    unittest.main()
