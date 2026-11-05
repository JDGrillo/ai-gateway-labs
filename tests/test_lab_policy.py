import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from scripts import lab_policy


GATEWAY_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test-rg"
    "/providers/Microsoft.ApiManagement/aigateways/test-gateway"
)
WORKSPACE_ID = GATEWAY_ID + "/workspaces/default"
ALIAS_ID = WORKSPACE_ID + "/aliases/internal-alias-id"
OTHER_POLICY = {
    "type": "requestRateLimit", "callsPerPeriod": 10, "periodSeconds": 60,
    "counterKey": ["identity"], "scope": "workspace",
}


class FakeArm:
    def __init__(self, policies=None, *, etag='"test-etag"', legacy_policies=None):
        self.policies = deepcopy(policies or [])
        self.legacy_policies = deepcopy(legacy_policies or [])
        self.etag = etag
        self.patches = []
        self.patch_targets = []
        self.error = None
        self.ignore_patch = False

    def request(self, method, resource_id, *, policies=None, etag=None):
        if resource_id not in (WORKSPACE_ID, ALIAS_ID):
            raise AssertionError("Wrong policy resource ID")
        attribute = "policies" if resource_id == WORKSPACE_ID else "legacy_policies"
        if method == "PATCH":
            if self.error:
                raise self.error
            if etag != self.etag:
                raise AssertionError("ETag not passed")
            self.patches.append(deepcopy(policies))
            self.patch_targets.append(resource_id)
            if not self.ignore_patch:
                setattr(self, attribute, deepcopy(policies))
        return {"properties": {"policies": deepcopy(getattr(self, attribute))}}, self.etag


class LabPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / ".lab-policy-state.json"
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def enable(self, client, count=1):
        lab_policy.enable(client, WORKSPACE_ID, count, self.state)

    def disable(self, client):
        lab_policy.inspect_saved(
            client, lab_policy.read_state(self.state), remove=True, state_path=self.state,
        )

    def test_enable_and_disable_preserve_other_policies(self):
        client = FakeArm([OTHER_POLICY])
        self.enable(client)
        policy = lab_policy.token_policy(1)
        self.assertEqual(client.patches, [[OTHER_POLICY, policy]])
        self.assertEqual(lab_policy.read_state(self.state), {"workspace_id": WORKSPACE_ID, "policy": policy})
        self.assertEqual(client.patch_targets, [WORKSPACE_ID])
        additional = {"type": "costLimit", "id": "other-budget", "amount": 5, "period": "day"}
        client.policies.append(additional)
        self.disable(client)
        self.assertEqual(client.policies, [OTHER_POLICY, additional])
        self.assertFalse(self.state.exists())

    def test_existing_token_limit_is_not_replaced(self):
        client = FakeArm([lab_policy.token_policy(500)])
        with self.assertRaisesRegex(RuntimeError, "already has a token limit"):
            self.enable(client)
        self.assertFalse(self.state.exists())
        self.assertEqual(client.patches, [])

    def test_enable_is_idempotent_for_saved_policy(self):
        client = FakeArm()
        self.enable(client)
        self.enable(client)
        self.assertEqual(len(client.patches), 1)

    def test_new_budget_or_target_cannot_overwrite_saved_state(self):
        client = FakeArm()
        self.enable(client)
        with self.assertRaisesRegex(RuntimeError, "Another lab policy"):
            self.enable(client, 2)
        state = lab_policy.read_state(self.state)
        state["workspace_id"] = WORKSPACE_ID.replace("test-gateway", "other-gateway")
        self.state.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Another lab policy"):
            self.enable(client)
        self.assertEqual(len(client.patches), 1)

    def test_changed_limit_is_not_deleted(self):
        client = FakeArm()
        self.enable(client)
        client.policies = [lab_policy.token_policy(100)]
        with self.assertRaisesRegex(RuntimeError, "differs from the saved"):
            self.disable(client)
        self.assertTrue(self.state.exists())
        self.assertEqual(len(client.patches), 1)

    def test_missing_policy_clears_state_without_patch(self):
        client = FakeArm()
        self.enable(client)
        client.policies = [OTHER_POLICY]
        self.disable(client)
        self.assertFalse(self.state.exists())
        self.assertEqual(client.policies, [OTHER_POLICY])
        self.assertEqual(len(client.patches), 1)

    def test_enable_failure_keeps_recovery_state(self):
        for error in (requests.Timeout("test timeout"), RuntimeError("concurrent update")):
            with self.subTest(error=type(error).__name__):
                client = FakeArm()
                client.error = error
                with self.assertRaises(type(error)):
                    self.enable(client)
                self.assertTrue(self.state.exists())
                client.error = None
                self.disable(client)
                self.assertFalse(self.state.exists())

    def test_disable_failure_keeps_state(self):
        client = FakeArm()
        self.enable(client)
        client.error = RuntimeError("concurrent update")
        with self.assertRaisesRegex(RuntimeError, "concurrent update"):
            self.disable(client)
        self.assertTrue(self.state.exists())

    def test_verify_writes_before_reporting_success_or_deleting_state(self):
        client = FakeArm()
        client.ignore_patch = True
        with self.assertRaisesRegex(RuntimeError, "did not return"):
            self.enable(client)
        self.assertTrue(self.state.exists())
        client.policies = [lab_policy.token_policy(1)]
        with self.assertRaisesRegex(RuntimeError, "still returns"):
            self.disable(client)
        self.assertTrue(self.state.exists())

    def test_etag_required_for_enable_and_disable(self):
        client = FakeArm(etag=None)
        with self.assertRaisesRegex(RuntimeError, "no ETag"):
            self.enable(client)
        self.assertFalse(self.state.exists())
        client.etag = '"test-etag"'
        self.enable(client)
        client.etag = None
        with self.assertRaisesRegex(RuntimeError, "no ETag"):
            self.disable(client)
        self.assertTrue(self.state.exists())

    def test_status_is_read_only(self):
        client = FakeArm()
        self.enable(client)
        lab_policy.inspect_saved(client, lab_policy.read_state(self.state))
        self.assertEqual(len(client.patches), 1)
        self.assertTrue(self.state.exists())

    def test_invalid_cleanup_state_is_rejected(self):
        for state in (
            {},
            {"workspace_id": "https://untrusted.example/workspace", "policy": lab_policy.token_policy(1)},
            {"workspace_id": WORKSPACE_ID, "policy": lab_policy.token_policy(0)},
            {"workspace_id": WORKSPACE_ID, "policy": lab_policy.token_policy(True)},
            {"workspace_id": WORKSPACE_ID, "policy": {"type": "costLimit", "count": 1}},
            {"alias_id": ALIAS_ID, "policy": lab_policy.token_policy(1)},
        ):
            with self.subTest(state=state):
                self.state.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(ValueError):
                    lab_policy.read_state(self.state)

    def test_no_state_does_not_authenticate_or_change_azure(self):
        with patch.object(lab_policy, "STATE_FILE", self.state), patch.object(lab_policy, "ArmClient") as client:
            lab_policy.main(["disable"])
            lab_policy.main(["status"])
            client.assert_not_called()

    def test_explicit_gateway_id_targets_workspace_without_alias(self):
        root = Path(self.directory.name)
        with (
            patch.object(lab_policy, "ROOT", root),
            patch.dict("os.environ", {}, clear=True),
            patch.object(lab_policy, "ArmClient") as client,
            patch.object(lab_policy, "enable") as enable,
        ):
            lab_policy.main(["enable", "--gateway-id", GATEWAY_ID])
            client.return_value.discover_gateway.assert_not_called()
            enable.assert_called_with(client.return_value, WORKSPACE_ID, 1)
            lab_policy.main(["enable", "--gateway-id", GATEWAY_ID, "--tokens-per-minute", "10"])
            enable.assert_called_with(client.return_value, WORKSPACE_ID, 10)

    def test_stale_legacy_alias_state_is_migrated(self):
        state = {"alias_id": ALIAS_ID, "policy": lab_policy.legacy_token_policy(1)}
        self.state.write_text(json.dumps(state), encoding="utf-8")
        client = FakeArm([OTHER_POLICY])
        self.enable(client)
        self.assertEqual(client.patch_targets, [WORKSPACE_ID])
        self.assertEqual(lab_policy.read_state(self.state), {
            "workspace_id": WORKSPACE_ID, "policy": lab_policy.token_policy(1),
        })

    def test_active_legacy_alias_state_requires_cleanup_before_gateway_enable(self):
        legacy = lab_policy.legacy_token_policy(1)
        self.state.write_text(json.dumps({"alias_id": ALIAS_ID, "policy": legacy}), encoding="utf-8")
        normalized = {"type": "tokenLimit", "count": 1, "period": "minute",
                      "counterKey": ["identity"], "scope": "resource"}
        client = FakeArm([OTHER_POLICY], legacy_policies=[normalized, {"type": "contentSafety"}])
        with self.assertRaisesRegex(RuntimeError, "earlier alias lab policy is still active"):
            self.enable(client)
        self.assertTrue(self.state.exists())
        self.assertEqual(client.patches, [])
        self.disable(client)
        self.assertEqual(client.patch_targets, [ALIAS_ID])
        self.assertEqual(client.legacy_policies, [{"type": "contentSafety"}])
        self.enable(client)
        self.assertEqual(client.patch_targets, [ALIAS_ID, WORKSPACE_ID])

    def test_modified_legacy_alias_policy_retains_state_and_prevents_gateway_change(self):
        self.state.write_text(json.dumps({
            "alias_id": ALIAS_ID, "policy": lab_policy.legacy_token_policy(1),
        }), encoding="utf-8")
        client = FakeArm(legacy_policies=[lab_policy.legacy_token_policy(2)])
        with self.assertRaisesRegex(RuntimeError, "differs from the saved"):
            self.enable(client)
        self.assertTrue(self.state.exists())
        self.assertEqual(client.patches, [])

    def test_positive_count(self):
        self.assertEqual(lab_policy.positive_count("1"), 1)
        for value in ("0", "-1", "2147483648"):
            with self.subTest(value=value), self.assertRaises(lab_policy.argparse.ArgumentTypeError):
                lab_policy.positive_count(value)

    def test_discovery_uses_shared_settings_and_explicit_endpoint_override(self):
        root = Path(self.directory.name)
        endpoint = "https://gateway.example.com/chat/completions"
        (root / ".env").write_text(f"AI_GATEWAY_CHAT_URL={endpoint}\n", encoding="utf-8")
        with (
            patch.object(lab_policy, "ROOT", root),
            patch.dict("os.environ", {}, clear=True),
            patch.object(lab_policy, "ArmClient") as client,
            patch.object(lab_policy, "enable") as enable,
        ):
            client.return_value.discover_gateway.return_value = GATEWAY_ID
            lab_policy.main(["enable"])
            client.return_value.discover_gateway.assert_called_with(endpoint)
            enable.assert_called_with(client.return_value, WORKSPACE_ID, 1)
            with patch.dict("os.environ", {"AI_GATEWAY_CHAT_URL": "https://shell.example/chat/completions"}):
                lab_policy.main(["enable"])
                client.return_value.discover_gateway.assert_called_with("https://shell.example/chat/completions")
                lab_policy.main(["enable", "--chat-url", endpoint])
                client.return_value.discover_gateway.assert_called_with(endpoint)

    def test_discovery_failure_cannot_enable_policy(self):
        with (
            patch.object(lab_policy, "ArmClient") as client,
            patch.object(lab_policy, "enable") as enable,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            client.return_value.discover_gateway.side_effect = RuntimeError("No matching gateway")
            with self.assertRaises(SystemExit) as error:
                lab_policy.main(["enable", "--chat-url", "https://example.com/chat/completions"])
            self.assertEqual(error.exception.code, 1)
            enable.assert_not_called()


class ArmClientTests(unittest.TestCase):
    def setUp(self):
        with patch.object(lab_policy, "access_token", return_value="synthetic-arm-token"):
            self.client = lab_policy.ArmClient()
        self.addCleanup(self.client.session.close)

    def response(self, body, *, status=200, etag=None):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode()
        if etag:
            response.headers["ETag"] = etag
        return response

    def test_preview_patch_shape_and_etag(self):
        policy = lab_policy.token_policy(1)
        with patch.object(self.client.session, "request", return_value=self.response(
            {"properties": {"policies": [policy]}}, etag='"updated"',
        )) as request:
            _, etag = self.client.request("PATCH", WORKSPACE_ID, policies=[policy], etag='"original"')
            self.assertEqual(etag, '"updated"')
            request.assert_called_once_with(
                "PATCH", f"{lab_policy.ARM}{WORKSPACE_ID}?api-version=2026-09-01-preview",
                headers={"If-Match": '"original"'},
                json={"properties": {"policies": [{
                    "type": "tokenLimit", "scope": "workspace", "count": 1,
                    "period": "minute", "counterKey": ["identity"],
                }]}},
                timeout=60, allow_redirects=False,
            )

    def test_gateway_discovery_matches_exact_hostname(self):
        other = {"id": GATEWAY_ID + "-other",
                 "properties": {"frontend": {"defaultHostname": "other.example.com"}}}
        target = {"id": GATEWAY_ID,
                  "properties": {"frontend": {"defaultHostname": "GATEWAY.EXAMPLE.COM."}}}
        with patch.object(lab_policy, "cli_value", return_value="00000000-0000-0000-0000-000000000000"), \
                patch.object(self.client.session, "get", return_value=self.response({"value": [other, target]})) as get:
            self.assertEqual(self.client.discover_gateway("https://gateway.example.com/chat/completions"), GATEWAY_ID)
            self.assertIn("/providers/Microsoft.ApiManagement/aigateways?", get.call_args.args[0])

    def test_gateway_discovery_refuses_missing_or_ambiguous_matches(self):
        target = {"id": GATEWAY_ID,
                  "properties": {"frontend": {"defaultHostname": "gateway.example.com"}}}
        for values in ([], [target, target], [
            {"id": GATEWAY_ID, "properties": {"frontend": {"defaultHostname": "gateway.example.com.attacker.test"}}},
        ]):
            with self.subTest(values=values), \
                    patch.object(lab_policy, "cli_value", return_value="00000000-0000-0000-0000-000000000000"), \
                    patch.object(self.client.session, "get", return_value=self.response({"value": values})):
                with self.assertRaisesRegex(RuntimeError, "exactly one"):
                    self.client.discover_gateway("https://gateway.example.com/chat/completions")

    def test_gateway_discovery_rejects_bad_endpoint_before_lookup(self):
        for endpoint in ("", "http://example.com/chat/completions", "https://example.com",
                         "https://user:password@example.com/chat/completions",
                         "https://example.com:8443/chat/completions"):
            with self.subTest(endpoint=endpoint), patch.object(lab_policy, "cli_value") as cli, \
                    patch.object(self.client.session, "get") as get:
                with self.assertRaises(ValueError):
                    self.client.discover_gateway(endpoint)
                cli.assert_not_called()
                get.assert_not_called()

    def test_gateway_discovery_pagination(self):
        collection = (f"{lab_policy.ARM}/subscriptions/00000000-0000-0000-0000-000000000000"
                      "/providers/Microsoft.ApiManagement/aigateways")
        target = {"id": GATEWAY_ID, "properties": {"frontend": {"defaultHostname": "gateway.example.com"}}}
        with patch.object(lab_policy, "cli_value", return_value="00000000-0000-0000-0000-000000000000"), \
                patch.object(self.client.session, "get", side_effect=[
                    self.response({"value": [], "nextLink": collection + "?api-version=2026-09-01-preview&skip=1"}),
                    self.response({"value": [target]}),
                ]) as get:
            self.assertEqual(self.client.discover_gateway("https://gateway.example.com/chat/completions"), GATEWAY_ID)
            self.assertEqual(get.call_count, 2)

    def test_resource_pagination(self):
        collection = f"{lab_policy.ARM}{GATEWAY_ID}/workspaces/default/aliases"
        url = f"{lab_policy.ARM}{GATEWAY_ID}/workspaces/default/aliases?api-version=2026-09-01-preview&skip=1"
        with patch.object(self.client.session, "get", side_effect=[
            self.response({"value": [], "nextLink": url}),
            self.response({"value": [{"id": ALIAS_ID, "properties": {"aliasName": "pool"}}]}),
        ]) as get:
            self.assertEqual(list(self.client.list_resources(collection)), [
                {"id": ALIAS_ID, "properties": {"aliasName": "pool"}},
            ])
            self.assertEqual(get.call_count, 2)

    def test_untrusted_or_repeated_pagination_is_rejected(self):
        for url in (
            "https://untrusted.example/aliases?api-version=2026-09-01-preview",
            f"{lab_policy.ARM}{GATEWAY_ID}/workspaces/default/aliases?api-version=2026-09-01-preview",
        ):
            with self.subTest(url=url), patch.object(
                self.client.session, "get", return_value=self.response({"value": [], "nextLink": url}),
            ) as get:
                with self.assertRaisesRegex(RuntimeError, "pagination"):
                    list(self.client.list_resources(f"{lab_policy.ARM}{GATEWAY_ID}/workspaces/default/aliases"))
                self.assertEqual(get.call_count, 1)

    def test_bad_resource_id_is_rejected_before_request(self):
        with patch.object(self.client.session, "request") as request:
            with self.assertRaises(ValueError):
                self.client.request("GET", "https://untrusted.example/alias")
            request.assert_not_called()

    def test_arm_errors_and_redirects_are_explicit(self):
        for status in (302, 401, 403, 412, 500):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.client.check_response(self.response({}, status=status))

    def test_arm_validation_error_includes_safe_code_and_field_not_raw_values(self):
        response = self.response({"error": {
            "code": "ValidationError",
            "message": "Do not print sensitive-data",
            "details": [
                {"target": "counterKey", "message": "Invalid field counterKey"},
                {"target": "/subscriptions/private-resource", "message": "sensitive-data"},
            ],
        }}, status=400)
        with self.assertRaises(RuntimeError) as error:
            self.client.check_response(response)
        self.assertIn("HTTP 400 (ValidationError)", str(error.exception))
        self.assertIn("counterKey", str(error.exception))
        self.assertNotIn("sensitive-data", str(error.exception))
        self.assertNotIn("private-resource", str(error.exception))

    def test_auth_token_is_captured_and_never_used_as_a_cli_argument(self):
        with (
            patch.object(lab_policy.shutil, "which", return_value="az"),
            patch.object(lab_policy.subprocess, "run", return_value=Mock(
                returncode=0, stdout="synthetic-arm-token\n",
            )) as run,
        ):
            self.assertEqual(lab_policy.access_token(), "synthetic-arm-token")
            self.assertNotIn("synthetic-arm-token", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
