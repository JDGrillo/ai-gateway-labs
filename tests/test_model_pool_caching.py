import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import requests


NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "03-model-pool.ipynb"
CHAT_URL = "https://gateway.example.com/default/models/openai/v1/chat/completions"


class ModelPoolCachingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cell = next(cell for cell in notebook["cells"] if cell.get("id") == "pool-requests")
        cls.code = compile("".join(cell["source"]), f"{NOTEBOOK.name}:pool-requests", "exec")

    @staticmethod
    def response(usage=None, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({
            "model": "mock-model",
            "choices": [{"message": {"content": "A gateway routes requests."}}],
            "usage": usage,
        }).encode("utf-8")
        return response

    def run_pool(self, responses):
        namespace = {
            "requests": requests,
            "chat_url": CHAT_URL,
            "api_key": "test-only",
            "alias": "test-chat-pool",
        }
        output = io.StringIO()
        with patch("requests.post", side_effect=responses) as post, contextlib.redirect_stdout(output):
            exec(self.code, namespace)
        return namespace, post.call_args_list, output.getvalue().splitlines()

    def test_repeated_prompt_and_reported_cache_usage(self):
        responses = [
            self.response({"prompt_tokens": 6000, "prompt_tokens_details": {"cached_tokens": 0}}),
            self.response({"prompt_tokens": 6000, "prompt_tokens_details": {"cached_tokens": 5888}}),
            self.response({"prompt_tokens": 6000}),
            self.response({"prompt_tokens": 6000, "prompt_tokens_details": None}),
            self.response(),
            self.response({"prompt_tokens": 6000, "prompt_tokens_details": {"cached_tokens": None}}),
        ]
        namespace, calls, lines = self.run_pool(responses)

        self.assertEqual(len(calls), 6)
        first_request = calls[0].kwargs["json"]
        self.assertEqual(first_request["model"], "test-chat-pool")
        prefix = (
            "An AI gateway routes requests to models and applies governance policies. "
            "It can provide authentication, observability, and traffic management. "
        ) * 200
        self.assertEqual(first_request["messages"], [{
            "role": "user",
            "content": prefix + "\nGive one short, original analogy for an AI gateway.",
        }])
        for call in calls:
            self.assertEqual(call.args[0], CHAT_URL)
            self.assertEqual(call.kwargs["json"], first_request)
            self.assertEqual(call.kwargs["timeout"], 90)

        observations = namespace["observations"]
        self.assertEqual([item["cached_tokens"] for item in observations], [0, 5888, None, None, None, None])
        self.assertEqual([item["prompt_tokens"] for item in observations], [6000, 6000, 6000, 6000, None, 6000])
        self.assertIn("prompt_tokens=6000 | cached_tokens=0", lines[0])
        self.assertIn("prompt_tokens=6000 | cached_tokens=5888", lines[1])
        self.assertIn("prompt_tokens=6000 | cached_tokens=not reported", lines[2])
        self.assertIn("prompt_tokens=not reported | cached_tokens=not reported", lines[4])

    def test_rate_limit_stops_without_hiding_completed_observations(self):
        responses = [
            self.response({"prompt_tokens": 6000, "prompt_tokens_details": {"cached_tokens": 1024}}),
            self.response(status=429),
        ]
        namespace, calls, lines = self.run_pool(responses)

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(namespace["observations"]), 1)
        self.assertEqual(namespace["observations"][0]["cached_tokens"], 1024)
        self.assertIn("Stopped at request 2 after a 429", lines[1])

    def test_other_http_errors_surface(self):
        with self.assertRaises(requests.HTTPError):
            self.run_pool([self.response(status=401)])


if __name__ == "__main__":
    unittest.main()
