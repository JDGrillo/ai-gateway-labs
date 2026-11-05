import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "02-governance.ipynb"
CHAT_URL = "https://gateway.example.com/default/models/openai/v1/chat/completions"


class NotebookPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.cells = {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}

    def setUp(self):
        self.namespace = {"lab_root": ROOT, "alias": "test-callable-alias", "chat_url": CHAT_URL}
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.run_cell("policy-settings")

    def run_cell(self, cell_id, namespace=None, source=None):
        code = self.cells[cell_id] if source is None else source
        exec(compile(code, f"{NOTEBOOK.name}:{cell_id}", "exec"),
             self.namespace if namespace is None else namespace)

    @staticmethod
    def result(returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_default_run_does_not_prompt_or_call_helper(self):
        self.namespace["use_temporary_policy"] = False
        self.assertEqual(self.namespace["tokens_per_minute"], 1)
        with patch("getpass.getpass") as prompt, patch("subprocess.run") as run:
            self.run_cell("policy-enable")
            self.run_cell("policy-cleanup")
        prompt.assert_not_called()
        run.assert_not_called()
        self.assertIn("setup skipped", self.output.getvalue())
        self.assertIn("cleanup skipped", self.output.getvalue())

    def test_opt_in_uses_current_endpoint_without_alias_or_prompt(self):
        self.namespace["use_temporary_policy"] = True
        self.namespace["tokens_per_minute"] = 20
        result = self.result(stdout="Gateway discovered.\n")
        with patch("getpass.getpass") as prompt, \
                patch("subprocess.run", return_value=result) as run:
            self.run_cell("policy-enable")
        prompt.assert_not_called()
        run.assert_called_once_with(
            [sys.executable, str(ROOT / "scripts" / "lab_policy.py"), "enable",
             "--chat-url", CHAT_URL,
             "--tokens-per-minute", "20"],
            capture_output=True, text=True, check=False,
        )
        self.assertIn("Gateway discovered", self.output.getvalue())
        self.assertNotIn("gateway_id", self.namespace)
        self.assertNotIn("policy_result", self.namespace)

    def test_enable_failure_is_visible_and_allows_cleanup(self):
        self.namespace["use_temporary_policy"] = True
        failure = self.result(returncode=1, stderr="No matching gateway in selected subscription.\n")
        with patch("getpass.getpass") as prompt, \
                patch("subprocess.run", side_effect=[failure, self.result(stdout="Removed.\n")]) as run, \
                patch("pathlib.Path.cwd", return_value=ROOT):
            with self.assertRaisesRegex(RuntimeError, "cleanup cell"):
                self.run_cell("policy-enable")
            self.run_cell("policy-cleanup")
        self.assertEqual(run.call_count, 2)
        prompt.assert_not_called()
        self.assertEqual(run.call_args.args[0][-1], "disable")
        self.assertIn("No matching gateway", self.output.getvalue())
        self.assertNotIn("gateway_id", self.namespace)

    def test_cleanup_uses_helper_after_opt_in(self):
        self.namespace["use_temporary_policy"] = True
        with patch("pathlib.Path.cwd", return_value=ROOT / "notebooks"), \
                patch("subprocess.run", return_value=self.result(stdout="Removed.\n")) as run:
            self.run_cell("policy-cleanup")
        run.assert_called_once_with(
            [sys.executable, str(ROOT / "scripts" / "lab_policy.py"), "disable"],
            capture_output=True, text=True, check=False,
        )

    def test_cleanup_after_restart_prefers_notebook_path(self):
        source = self.cells["policy-cleanup"]
        self.assertEqual(source.count("cleanup_previous_policy = False"), 1)
        source = source.replace("cleanup_previous_policy = False", "cleanup_previous_policy = True", 1)
        namespace = {"__vsc_ipynb_file__": str(NOTEBOOK)}
        with tempfile.TemporaryDirectory() as unrelated, \
                patch("pathlib.Path.cwd", return_value=Path(unrelated)), \
                patch("subprocess.run", return_value=self.result()) as run:
            self.run_cell("policy-cleanup", namespace=namespace, source=source)
        self.assertEqual(run.call_args.args[0], [
            sys.executable, str(ROOT / "scripts" / "lab_policy.py"), "disable",
        ])

    def test_cleanup_reports_missing_repository_and_does_not_run(self):
        namespace = {"use_temporary_policy": True}
        with tempfile.TemporaryDirectory() as unrelated, \
                patch("pathlib.Path.cwd", return_value=Path(unrelated)), \
                patch("subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Could not find the policy helper"):
                self.run_cell("policy-cleanup", namespace=namespace)
        run.assert_not_called()

    def test_cleanup_failure_surfaces_details_and_keeps_recovery_instructions(self):
        self.namespace["use_temporary_policy"] = True
        failure = self.result(returncode=1, stderr="ETag conflict; retry.\n")
        with patch("pathlib.Path.cwd", return_value=ROOT), \
                patch("subprocess.run", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "Keep the local cleanup state"):
                self.run_cell("policy-cleanup")
        self.assertIn("ETag conflict", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
