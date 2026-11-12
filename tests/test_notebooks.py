import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values
import requests


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.ipynb"))
EXAMPLE_CONFIG = dotenv_values(ROOT / ".env.example")
CONFIG = {
    "AI_GATEWAY_CHAT_URL": "https://gateway.example.com/default/models/openai/v1/chat/completions",
    "AI_GATEWAY_API_KEY": "test-only",
    "AI_GATEWAY_MODEL_ALIAS": "test-chat-pool",
}


class NotebookTests(unittest.TestCase):
    def run_notebook(self, notebook, *, status=200, cwd="root", overrides=None):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({
            "choices": [{"message": {"content": "A gateway routes requests."}}],
            "model": "mock-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        settings = dict(CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notebooks").mkdir()
            (root / ".env").write_text(
                "\n".join(f"{key}={value}" for key, value in settings.items()),
                encoding="utf-8",
            )
            workdir = root if cwd == "root" else root / "notebooks"
            with (
                patch.dict(os.environ, overrides or {}, clear=True),
                patch("pathlib.Path.cwd", return_value=workdir),
                patch("requests.post", return_value=response) as post,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                namespace = {}
                content = json.loads(notebook.read_text(encoding="utf-8"))
                for index, cell in enumerate(content["cells"]):
                    if cell["cell_type"] == "code":
                        self.assertIsNone(cell["execution_count"])
                        self.assertEqual(cell["outputs"], [])
                        code = compile("".join(cell["source"]), f"{notebook.name}:{index}", "exec")
                        exec(code, namespace)
                        if cell.get("id") == "policy-settings":
                            namespace["use_temporary_policy"] = False
                return post.call_args_list

    def test_requests_from_root_and_notebooks_directory(self):
        for notebook, expected_count in zip(NOTEBOOKS, (1, 9, 6)):
            for cwd in ("root", "notebooks"):
                with self.subTest(notebook=notebook.name, cwd=cwd):
                    calls = self.run_notebook(notebook, cwd=cwd)
                    self.assertEqual(len(calls), expected_count)
                    for call in calls:
                        self.assertEqual(call.args[0], CONFIG["AI_GATEWAY_CHAT_URL"])
                        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-only")
                        self.assertEqual(call.kwargs["json"]["model"], CONFIG["AI_GATEWAY_MODEL_ALIAS"])
                        self.assertEqual(call.kwargs["timeout"], 90)

    def test_shell_overrides_dotenv(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                calls = self.run_notebook(notebook, overrides={
                    "AI_GATEWAY_API_KEY": "shell-test-only",
                    "AI_GATEWAY_MODEL_ALIAS": "custom-chat-alias",
                    "AI_GATEWAY_CHAT_URL": "https://override.example.com/chat/completions",
                })
                self.assertEqual(calls[0].kwargs["headers"]["Authorization"], "Bearer shell-test-only")
                self.assertEqual(
                    calls[0].kwargs["json"]["model"],
                    "custom-chat-alias",
                )
                self.assertEqual(calls[0].args[0], "https://override.example.com/chat/completions")

    def test_missing_key_and_invalid_url_fail_before_request(self):
        for notebook in NOTEBOOKS:
            for overrides, error in (
                ({"AI_GATEWAY_API_KEY": ""}, RuntimeError),
                ({"AI_GATEWAY_CHAT_URL": ""}, RuntimeError),
                ({"AI_GATEWAY_MODEL_ALIAS": ""}, RuntimeError),
                ({"AI_GATEWAY_CHAT_URL": "http://invalid/chat/completions"}, ValueError),
                ({"AI_GATEWAY_CHAT_URL": "https://gateway.example.com/responses"}, ValueError),
            ):
                with self.subTest(notebook=notebook.name, overrides=overrides):
                    with self.assertRaises(error):
                        self.run_notebook(notebook, overrides=overrides)

    def test_blank_template_fails_explicitly(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                with self.assertRaisesRegex(RuntimeError, "AI_GATEWAY_MODEL_ALIAS"):
                    self.run_notebook(notebook, overrides=dict(EXAMPLE_CONFIG))

    def test_legacy_pool_setting_does_not_override_shared_alias(self):
        calls = self.run_notebook(NOTEBOOKS[2], overrides={"AI_GATEWAY_POOL_ALIAS": "stale-pool"})
        self.assertEqual(calls[0].kwargs["json"]["model"], CONFIG["AI_GATEWAY_MODEL_ALIAS"])

    def test_limit_stops_governance_and_pool(self):
        for notebook in NOTEBOOKS[1:]:
            with self.subTest(notebook=notebook.name):
                self.assertEqual(len(self.run_notebook(notebook, status=429)), 1)

    def test_auth_failures_surface(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                with self.assertRaises(requests.HTTPError):
                    self.run_notebook(notebook, status=401)

    def test_template_contains_only_three_blank_shared_settings(self):
        self.assertEqual(set(EXAMPLE_CONFIG), set(CONFIG))
        self.assertTrue(all(value == "" for value in EXAMPLE_CONFIG.values()))
        self.assertEqual(len(NOTEBOOKS), 3)

    def test_notebook_cells_have_unique_ids(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                content = json.loads(notebook.read_text(encoding="utf-8"))
                identifiers = [cell.get("id") for cell in content["cells"]]
                self.assertEqual(len(identifiers), len(set(identifiers)))
                for identifier in identifiers:
                    self.assertIsInstance(identifier, str)
                    self.assertRegex(identifier, r"^[A-Za-z0-9_-]{1,64}$")


if __name__ == "__main__":
    unittest.main()
