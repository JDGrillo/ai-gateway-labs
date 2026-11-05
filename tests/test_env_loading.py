import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.ipynb"))
SETTINGS = {
    "AI_GATEWAY_CHAT_URL": "https://gateway.example.com/default/models/openai/v1/chat/completions",
    "AI_GATEWAY_API_KEY": "fixture-key",
    "AI_GATEWAY_MODEL_ALIAS": "fixture-model",
}


class EnvironmentLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        (self.root / "notebooks").mkdir(parents=True)
        (self.root / ".env.example").write_text("", encoding="utf-8")
        self.env_path = self.root / ".env"
        self.write_env(SETTINGS)

    def write_env(self, values, encoding="utf-8"):
        self.env_path.write_text(
            "\n".join(f"{name}={value}" for name, value in values.items()),
            encoding=encoding,
        )

    def setup_code(self, notebook):
        content = json.loads(notebook.read_text(encoding="utf-8"))
        cell = next(cell for cell in content["cells"] if cell["cell_type"] == "code")
        return compile("".join(cell["source"]), notebook.name, "exec")

    def run_setup(self, notebook, cwd, environment=None, namespace=None):
        namespace = {} if namespace is None else namespace
        environment = {} if environment is None else environment
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("pathlib.Path.cwd", return_value=cwd),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(self.setup_code(notebook), namespace)
            for name in SETTINGS:
                self.assertEqual(os.environ.get(name), environment.get(name))
        return namespace

    def test_discovers_env_from_project_notebooks_and_nested_directories(self):
        nested = self.root / "nested" / "deeper"
        nested.mkdir(parents=True)
        for notebook in NOTEBOOKS:
            for cwd in (self.root, self.root / "notebooks", nested):
                with self.subTest(notebook=notebook.name, cwd=cwd):
                    namespace = self.run_setup(notebook, cwd)
                    self.assertEqual(namespace["env_path"], self.env_path)
                    self.assertEqual(namespace["api_key"], SETTINGS["AI_GATEWAY_API_KEY"])
                    self.assertEqual(namespace["alias"], SETTINGS["AI_GATEWAY_MODEL_ALIAS"])
                    self.assertNotIn("file_settings", namespace)
                    self.assertNotIn("settings", namespace)

    def test_prefers_vscode_notebook_location_over_unrelated_working_directory(self):
        other = Path(self.temporary.name) / "other-project"
        (other / "notebooks").mkdir(parents=True)
        (other / ".env.example").write_text("", encoding="utf-8")
        (other / ".env").write_text("AI_GATEWAY_MODEL_ALIAS=wrong-project", encoding="utf-8")
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                namespace = {
                    "__vsc_ipynb_file__": str(self.root / "notebooks" / notebook.name),
                }
                result = self.run_setup(notebook, other, namespace=namespace)
                self.assertEqual(result["env_path"], self.env_path)
                self.assertEqual(result["alias"], SETTINGS["AI_GATEWAY_MODEL_ALIAS"])

    def test_rerunning_setup_reloads_file_without_mutating_environment(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                self.write_env(SETTINGS)
                namespace = {}
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch("pathlib.Path.cwd", return_value=self.root),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = self.setup_code(notebook)
                    exec(code, namespace)
                    changed = dict(SETTINGS, AI_GATEWAY_MODEL_ALIAS="updated-model")
                    self.write_env(changed)
                    exec(code, namespace)
                    self.assertEqual(namespace["alias"], "updated-model")
                    self.assertTrue(all(name not in os.environ for name in SETTINGS))

    def test_identifies_only_the_missing_setting_and_checked_file(self):
        self.write_env(dict(SETTINGS, AI_GATEWAY_API_KEY=""))
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                with self.assertRaises(RuntimeError) as error:
                    self.run_setup(notebook, self.root)
                message = str(error.exception)
                self.assertTrue(message.startswith("Missing or blank settings: AI_GATEWAY_API_KEY."))
                self.assertIn(f"Checked {self.env_path} (found)", message)
                self.assertNotIn(SETTINGS["AI_GATEWAY_API_KEY"], message)

    def test_blank_environment_override_is_explained(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                with self.assertRaises(RuntimeError) as error:
                    self.run_setup(notebook, self.root, {"AI_GATEWAY_API_KEY": ""})
                self.assertIn("Blank kernel environment variables override .env: AI_GATEWAY_API_KEY",
                              str(error.exception))
                self.assertNotIn(SETTINGS["AI_GATEWAY_API_KEY"], str(error.exception))

    def test_nonempty_environment_values_override_file(self):
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                result = self.run_setup(notebook, self.root, {
                    "AI_GATEWAY_MODEL_ALIAS": "shell-model",
                })
                self.assertEqual(result["alias"], "shell-model")

    def test_shell_settings_work_without_env_file(self):
        self.env_path.unlink()
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                result = self.run_setup(notebook, self.root, SETTINGS)
                self.assertEqual(result["alias"], SETTINGS["AI_GATEWAY_MODEL_ALIAS"])

    def test_missing_env_file_is_reported(self):
        self.env_path.unlink()
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                with self.assertRaises(RuntimeError) as error:
                    self.run_setup(notebook, self.root)
                self.assertIn(f"Checked {self.env_path} (not found)", str(error.exception))
                for name in SETTINGS:
                    self.assertIn(name, str(error.exception))

    def test_utf8_bom_is_supported(self):
        self.write_env(SETTINGS, encoding="utf-8-sig")
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                result = self.run_setup(notebook, self.root)
                self.assertEqual(result["chat_url"], SETTINGS["AI_GATEWAY_CHAT_URL"])

    def test_notebook_directory_fallback_without_template(self):
        (self.root / ".env.example").unlink()
        for notebook in NOTEBOOKS:
            with self.subTest(notebook=notebook.name):
                result = self.run_setup(notebook, self.root / "notebooks")
                self.assertEqual(result["env_path"], self.env_path)


if __name__ == "__main__":
    unittest.main()
