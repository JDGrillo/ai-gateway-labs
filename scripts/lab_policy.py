"""Temporarily limit a dedicated AI Gateway using its workspace policy."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlparse

from dotenv import dotenv_values
import requests


ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".lab-policy-state.json"
ARM = "https://management.azure.com"
API_VERSION = "2026-09-01-preview"
GATEWAY_PATTERN = (
    r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/?#]+"
    r"/providers/Microsoft\.ApiManagement/aigateways/[a-z0-9-]+"
)
WORKSPACE_PATTERN = GATEWAY_PATTERN + r"/workspaces/default"
ALIAS_PATTERN = WORKSPACE_PATTERN + r"/aliases/[a-zA-Z0-9_.-]+"


def token_policy(count):
    return {
        "type": "tokenLimit",
        "scope": "workspace",
        "count": count,
        "period": "minute",
        "counterKey": ["identity"],
    }


def legacy_token_policy(count):
    return {"type": "tokenLimit", "count": count, "period": "minute", "counterKey": "Identity"}


def positive_count(value):
    count = int(value)
    if not 1 <= count <= 2_147_483_647:
        raise argparse.ArgumentTypeError("Use a token count from 1 to 2147483647.")
    return count


def cli_value(*arguments):
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required. Install it and run az login.")
    result = subprocess.run(
        [az, *arguments],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Azure CLI account lookup failed. Run az login and check your selected subscription.")
    return result.stdout.strip()


def access_token():
    return cli_value("account", "get-access-token", "--resource", ARM + "/",
                     "--query", "accessToken", "--output", "tsv")


class ArmClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {access_token()}"

    def request(self, method, resource_id, *, policies=None, etag=None):
        if not (re.fullmatch(WORKSPACE_PATTERN, resource_id, re.IGNORECASE)
                or re.fullmatch(ALIAS_PATTERN, resource_id, re.IGNORECASE)):
            raise ValueError("Expected a dedicated AI Gateway workspace or legacy alias resource ID.")
        headers = {"If-Match": etag} if etag else {}
        response = self.session.request(
            method, f"{ARM}{resource_id}?api-version={API_VERSION}",
            headers=headers,
            json={"properties": {"policies": policies}} if policies is not None else None,
            timeout=60, allow_redirects=False,
        )
        self.check_response(response)
        resource = response.json()
        return resource, response.headers.get("ETag") or resource.get("eTag")

    @staticmethod
    def check_response(response):
        if response.status_code == 412:
            raise RuntimeError("The policy target changed concurrently. Nothing was overwritten; rerun the command.")
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error", {}) if isinstance(body, dict) else {}
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,80}", code) else None
            details = error.get("details", [])
            fields = [
                detail["target"] for detail in details
                if isinstance(detail, dict) and isinstance(detail.get("target"), str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,80}", detail["target"])
            ] if isinstance(details, list) else []
            context = f" ({code})" if code else ""
            if fields:
                context += f"; invalid field(s): {', '.join(dict.fromkeys(fields))}"
            raise RuntimeError(
                f"ARM returned HTTP {response.status_code}{context}. Check Azure Activity Log for details, "
                f"gateway/workspace permissions, and support for API version {API_VERSION}. "
                "Local cleanup state is retained if present."
            )

    def list_resources(self, collection):
        url = f"{collection}?api-version={API_VERSION}"
        seen = set()
        while url:
            if not url.startswith(collection + "?") or url in seen:
                raise RuntimeError("Unexpected resource pagination URL; refusing to send Azure credentials.")
            seen.add(url)
            response = self.session.get(url, timeout=60, allow_redirects=False)
            self.check_response(response)
            page = response.json()
            yield from page["value"]
            url = page.get("nextLink")

    def discover_gateway(self, chat_url):
        parsed = urlparse(chat_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.path.endswith("/chat/completions")):
            raise ValueError("Set AI_GATEWAY_CHAT_URL to the full HTTPS Chat completions endpoint.")
        subscription = cli_value("account", "show", "--query", "id", "--output", "tsv")
        if not re.fullmatch(r"[0-9a-f-]{36}", subscription, re.IGNORECASE):
            raise RuntimeError("Azure CLI did not return a valid selected subscription ID.")
        collection = f"{ARM}/subscriptions/{subscription}/providers/Microsoft.ApiManagement/aigateways"
        hostname = parsed.hostname.lower().rstrip(".")
        matches = [
            item["id"] for item in self.list_resources(collection)
            if (item.get("properties", {}).get("frontend", {}).get("defaultHostname") or "")
            .lower().rstrip(".") == hostname
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Expected exactly one dedicated AI Gateway matching AI_GATEWAY_CHAT_URL in the "
                "selected Azure CLI subscription. Check the endpoint and az account set --subscription "
                "'<subscription-id>'. For a custom hostname or resource-scoped access, use --gateway-id."
            )
        if not re.fullmatch(GATEWAY_PATTERN, matches[0], re.IGNORECASE):
            raise RuntimeError("Gateway discovery returned an invalid resource ID; nothing was changed.")
        return matches[0]

def policies_from(resource):
    policies = resource.get("properties", {}).get("policies", [])
    if not isinstance(policies, list) or not all(isinstance(item, dict) for item in policies):
        raise RuntimeError("Unexpected policies response; nothing will be overwritten.")
    return policies


def read_state(path):
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or set(state) not in (
        {"workspace_id", "policy"}, {"alias_id", "policy"},
    ):
        raise ValueError("Invalid cleanup state. Inspect the local state file before changing policies.")
    legacy = "alias_id" in state
    target = state["alias_id"] if legacy else state["workspace_id"]
    policy = state["policy"]
    if (not isinstance(target, str)
            or not re.fullmatch(ALIAS_PATTERN if legacy else WORKSPACE_PATTERN, target, re.IGNORECASE)
            or not isinstance(policy, dict)
            or type(policy.get("count")) is not int
            or not 1 <= policy["count"] <= 2_147_483_647
            or policy != (legacy_token_policy(policy["count"]) if legacy else token_policy(policy["count"]))):
        raise ValueError("Invalid cleanup state. Inspect the local state file before changing policies.")
    return state


def state_target(state):
    return state["alias_id"] if "alias_id" in state else state["workspace_id"]


def saved_policy_present(policies, policy):
    limits = [item for item in policies if item.get("type") == "tokenLimit"]
    if not limits:
        return False
    expected = dict(policy)
    if expected.get("counterKey") == "Identity":
        expected["counterKey"] = ["identity"]
        expected["scope"] = "resource"
    actual = dict(limits[0])
    if actual.get("counterKey") == "Identity":
        actual["counterKey"] = ["identity"]
    if expected.get("scope") == "resource" and "scope" not in actual:
        actual["scope"] = "resource"
    if len(limits) != 1 or actual != expected:
        raise RuntimeError("The token limit differs from the saved lab policy. Refusing to change it; state retained.")
    return True


def enable(client, workspace_id, count, state_path=STATE_FILE):
    if not re.fullmatch(WORKSPACE_PATTERN, workspace_id, re.IGNORECASE):
        raise ValueError("Expected the default workspace of a dedicated AI Gateway.")
    policy = token_policy(count)
    if state_path.exists():
        state = read_state(state_path)
        if "alias_id" in state:
            previous, _ = client.request("GET", state["alias_id"])
            if saved_policy_present(policies_from(previous), state["policy"]):
                raise RuntimeError("An earlier alias lab policy is still active. Run the cleanup cell before enabling the gateway limit.")
            state_path.unlink()
            print("Previous alias lab policy is absent; its stale recovery record was cleared.")
        elif state != {"workspace_id": workspace_id, "policy": policy}:
            raise RuntimeError("Another lab policy is recorded. Disable it before enabling a new one.")
    resource, etag = client.request("GET", workspace_id)
    policies = policies_from(resource)
    if state_path.exists():
        if saved_policy_present(policies, policy):
            print("The saved lab token limit is already enabled.")
            return
        raise RuntimeError("Cleanup state exists but its policy is absent. Run disable to clear the state first.")
    if any(item.get("type") == "tokenLimit" for item in policies):
        raise RuntimeError("This gateway workspace already has a token limit. It will not be replaced.")
    if not etag:
        raise RuntimeError("The gateway workspace has no ETag. Refusing an unconditional update.")
    # Persist ownership before PATCH so a timeout or interrupted process remains recoverable.
    with state_path.open("x", encoding="utf-8") as file:
        json.dump({"workspace_id": workspace_id, "policy": policy}, file, indent=2)
        file.write("\n")
    client.request("PATCH", workspace_id, policies=[*policies, policy], etag=etag)
    current, _ = client.request("GET", workspace_id)
    if not saved_policy_present(policies_from(current), policy):
        raise RuntimeError("ARM did not return the new policy. Inspect the gateway workspace; cleanup state retained.")
    print(f"Enabled {count} token(s)/minute per identity across the gateway workspace.")
    print("Allow configuration to propagate before running notebook 2. Run disable afterward.")


def inspect_saved(client, state, *, remove=False, state_path=STATE_FILE):
    target = state_target(state)
    resource, etag = client.request("GET", target)
    policies = policies_from(resource)
    present = saved_policy_present(policies, state["policy"])
    if not remove:
        print("Saved lab policy is enabled." if present else "Saved lab policy is absent; run disable to clear local state.")
        return
    if present:
        if not etag:
            raise RuntimeError("The policy target has no ETag. Refusing an unconditional update.")
        remaining = [item for item in policies if item.get("type") != "tokenLimit"]
        client.request("PATCH", target, policies=remaining, etag=etag)
        current, _ = client.request("GET", target)
        if saved_policy_present(policies_from(current), state["policy"]):
            raise RuntimeError("ARM still returns the lab policy. Cleanup state retained; rerun disable.")
    state_path.unlink()
    print("Lab policy is absent; local cleanup state removed. Unrelated policies were preserved.")
    print("Allow configuration to propagate before using the alias again.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("enable", help="Add a temporary token limit; never replace an existing limit.")
    create.add_argument("--gateway-id", help="Optional explicit ARM resource ID; otherwise discover from the chat URL.")
    create.add_argument("--chat-url", help="Chat endpoint for discovery; defaults to AI_GATEWAY_CHAT_URL in .env or shell.")
    create.add_argument("--tokens-per-minute", type=positive_count, default=1)
    commands.add_parser("status", help="Check the policy recorded in local cleanup state.")
    commands.add_parser("disable", help="Remove only the unchanged policy recorded by enable.")
    args = parser.parse_args(argv)
    try:
        if args.action == "enable":
            settings = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
            client = ArmClient()
            if args.gateway_id:
                gateway_id = args.gateway_id.rstrip("/")
            else:
                chat_url = (args.chat_url if args.chat_url is not None else
                            os.environ.get("AI_GATEWAY_CHAT_URL", settings.get("AI_GATEWAY_CHAT_URL") or "")).strip()
                gateway_id = client.discover_gateway(chat_url)
                print("Gateway discovered from the configured chat endpoint.")
            if not re.fullmatch(GATEWAY_PATTERN, gateway_id, re.IGNORECASE):
                raise ValueError("Expected the full ARM resource ID of a dedicated AI Gateway, not its URL or name.")
            enable(client, gateway_id + "/workspaces/default", args.tokens_per_minute)
        elif STATE_FILE.exists():
            state = read_state(STATE_FILE)
            inspect_saved(ArmClient(), state, remove=args.action == "disable")
        else:
            print("No saved lab policy state. No Azure policy was inspected or removed.")
    except (OSError, ValueError, RuntimeError, requests.RequestException, subprocess.SubprocessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
