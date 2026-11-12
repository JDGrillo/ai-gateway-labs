# AI Gateway labs

This deploys the **dedicated AI Gateway** (`Microsoft.ApiManagement/aigateways`), not a traditional API Management service (`Microsoft.ApiManagement/service`). Terraform uses AzureRM for the resource group and AzAPI for the preview gateway resource. It reuses your existing Microsoft Foundry account and model deployments; it does not provision models or a second APIM service. AI Gateway is a public preview, currently available in **East US 2** and **Sweden Central**. The template uses the provider's default SKU rather than assuming an undocumented SKU name or price; check the portal pricing before deployment.

## Run the labs

Prerequisites: Python 3 with pip, access to a dedicated AI Gateway, and at least one imported model supporting Chat Completions. The pooling notebook requires two compatible model targets. If you do not have a gateway, follow **Provision a new gateway** and **Connect your Foundry account** below first.

Clone or download this repository and open a terminal in its root directory. The generic [environment template](.env.example) contains only **three shared settings**. Keep deployment-specific values and credentials in the root `.env`, which is ignored by Git.

| Setting | Where to find it in the [AI Gateway portal](https://ai.gateway.azure.com) |
| --- | --- |
| `AI_GATEWAY_CHAT_URL` | Open the model's **Overview** and copy the full **Chat completions** HTTPS endpoint. Do not construct it from the hostname. |
| `AI_GATEWAY_API_KEY` | **Access > Keys > Create API key**. Use a gateway runtime key, not a Foundry key or APIM subscription key. Runtime keys grant gateway-wide access; protect and revoke them when no longer needed. |
| `AI_GATEWAY_MODEL_ALIAS` | **Models > Aliases**: copy the callable alias (`aliasName`), not its ARM resource name, resource ID, or backend deployment name. |

All three notebooks use these same settings. The alias can target one model or a pool; notebook 3 requires a pool with two compatible chat targets. Subscription IDs, resource groups, provider IDs, backend resource IDs, and other protocol endpoints are not needed in the notebook environment. The optional policy exercise discovers the gateway from the configured chat endpoint and selected Azure CLI subscription. Embeddings and Anthropic Messages models cannot substitute for Chat Completions targets in these labs.

From the repository root:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
  Copy-Item -LiteralPath .env.example -Destination .env
}
# Edit .env and fill in the three settings from the portal.
python -m pip install -r requirements.txt
python -m jupyter lab
```

The notebooks read the root `.env` on each setup-cell run without adding its values to the kernel environment. They find the repository from its notebook path in a local VS Code kernel, or by searching the kernel's working directory and its parents. If the repository cannot be found, open it in VS Code or change the Jupyter kernel's working directory to the repository root.

Existing kernel environment variables take precedence over `.env`, **even if blank**. After editing `.env`, rerun the setup cell; if an older notebook run already loaded values into the kernel environment, restart the kernel first. Restart Jupyter itself after changing shell variables. Missing-setting errors name the blank or missing settings and the file checked without displaying values. Never save real keys or request headers in notebook output.

## Work through the notebooks

In Jupyter, open the notebooks in the order below and run each one's cells from top to bottom using a Python 3 kernel with the installed requirements. Use a dedicated runtime key and a non-production alias. A dedicated key is still gateway-wide, not restricted to that alias. One run of all three notebooks makes at most **16 model requests** and incurs usage charges; rerunning cells makes additional requests. Do **not** reprovision an existing gateway just to run the lab.

| Notebook | Exercise | Expected result and caveat |
| --- | --- | --- |
| 1. [Model access](notebooks/01-model-access.ipynb) | Inspect the model alias and backend target(s) in the portal, then send a chat request through the gateway. | The output includes the gateway host, alias, answer, and any reported token usage. One request uses a gateway runtime key, not a Foundry credential. A response alone does not prove which backend answered. |
| 2. [Governance](notebooks/02-governance.ipynb) | Observe request status and reported token counts, then compare them with gateway policy and telemetry. | At most nine requests. Optional notebook cells can enable and remove a temporary token limit; without a limit, all may succeed. A `429` can also come from backend quota, so check its source. |
| 3. [Model pool](notebooks/03-model-pool.ipynb) | Configure an alias with two compatible chat targets as described below, send requests to that alias, and inspect the reported model IDs, prompt and cached token counts, and per-backend telemetry. | At most six requests with long prompts; these use more tokens and can cost more. Equal weights do not guarantee an even split in a small sample; response text or a reported model ID alone is not definitive backend-routing evidence. |

Use the same alias for all three notebooks; it may be a pool. To compare a pool with a single model in notebooks 1 and 2, choose a single-model chat alias and change `AI_GATEWAY_MODEL_ALIAS`. Changing a shared alias or consumption policy can affect other applications; use isolated, non-production configuration.

Local regression checks (mocked requests, no Azure traffic or keys required):

```powershell
python -m unittest discover -s tests -v
```

## Temporarily enable a lab token limit (optional)

The [governance notebook](notebooks/02-governance.ipynb) can run [the policy helper](scripts/lab_policy.py) before its requests and remove the limit afterward. It applies a token limit **across the gateway's default workspace**, affecting every model and caller, including callers using other aliases or runtime keys. Use an **isolated non-production gateway**, not a shared production gateway.

The helper requires Azure CLI (`az login`), permission to list gateways in the selected subscription, and permission to read/update the default workspace. It uses your Azure CLI management-plane identity, **not** the runtime key. No additional `.env` settings are required: it discovers the gateway by matching `AI_GATEWAY_CHAT_URL` against gateway frontend hostnames, with shell values taking precedence. It never picks the first available gateway or guesses from an alias name. No match or ambiguous matches fail before any policy is changed. Select the correct subscription with `az account set --subscription '<subscription-id>'`.

In notebook 2:

1. Run the initial setup cell. Set `use_temporary_policy = True` in the optional policy settings cell, then run that cell and the following enable cell. Gateway discovery is automatic; no resource ID is entered or saved in notebook source.
2. Allow time for the policy to propagate, then run the request cell.
3. Run the cleanup cell after the exercise, even if an earlier cell failed. If the kernel restarted, set `cleanup_previous_policy = True` in that cell and run it directly. A failed cell can stop Run All before cleanup.

Policy changes require the explicit `use_temporary_policy` opt-in. When opted in, Run All reaches the cleanup cell only if preceding cells succeed; the separate recovery option also works after a restart. Set both notebook flags to `False` before saving a public copy. The policy has no automatic expiration.

Alternatively, run the helper from the repository root in PowerShell:

```powershell
az login
# Discovers the existing gateway using the shared .env and selected subscription.
python scripts\lab_policy.py enable
python scripts\lab_policy.py status
# Now run notebook 2, then remove the temporary limit even if the notebook fails:
python scripts\lab_policy.py disable
```

For custom endpoint hostnames or an identity unable to list gateways, the CLI retains an optional `--gateway-id '<full-dedicated-gateway-resource-id>'` override. Obtain it from Azure resource Properties or `terraform -chdir=infra output -raw gateway_id`. Normal notebook execution does not need this override, Terraform, or a pasted resource ID.

The default is **one token per minute per authenticated identity across the workspace**, not one request per minute or a separate budget for each alias. A short chat normally exceeds this budget, making a `429` likely during the bounded request loop. The first request may succeed or be rejected. Configuration propagation, counter windows, and backend quota mean a particular request number or `429` is not guaranteed. Verify the policy and telemetry rather than increasing load. Use `--tokens-per-minute <positive-integer>` to change the budget.

The helper uses `PATCH` on the default workspace with `properties.policies` and an `If-Match` ETag, using `2026-09-01-preview`, the version supported by this lab's gateway. It sends `scope: "workspace"` and `counterKey: ["identity"]` to aggregate the limit across the workspace by caller identity. See the [workspace update API](https://learn.microsoft.com/rest/api/aigateway/ai-gateway-workspace-resources/update) for the management operation.

Safety and cleanup:

- An existing workspace token limit is **never replaced**. Unrelated workspace policies, alias policies, and backend targets are preserved; other applicable policies remain in force.
- Before applying, the helper writes the workspace resource ID and its own policy to the Git-ignored root `.lab-policy-state.json`. It stores no runtime key or Azure access token. Keep this file until cleanup succeeds; it supports recovery after an interrupted or failed request. The helper also recognizes cleanup records created by the earlier alias-scoped version.
- `disable` reads the saved target, not your current `.env`, removes only an unchanged lab token limit, verifies its absence, then deletes the local state. Concurrent ETag conflicts or a manually modified token limit cause an explicit error instead of overwriting configuration. Resolve the conflict and retry; do not delete the state as a substitute for Azure cleanup.
- Allow time for changes to reach the runtime after enabling or disabling. If cleanup cannot run from this checkout, inspect the saved target and remove the lab limit in the portal without removing other policies. Losing the state does **not** remove the Azure policy. There is no automatic expiration.
- Run `disable` before notebooks 1 and 3 to avoid carrying the temporary limit into those exercises.

## Provision a new gateway (optional)

Prerequisites: Terraform 1.5+, Azure CLI (authenticated with `az login`), a subscription with Contributor access to create a new resource group, and the `Microsoft.ApiManagement` provider registered. Your existing Foundry account must have a publicly accessible endpoint and at least one deployment supporting Chat Completions. For model import you also need Reader and User Access Administrator (or equivalent) on that account.

In PowerShell, from this directory:

```powershell
az login
az account set --subscription '<subscription-id>'
az provider register --namespace Microsoft.ApiManagement
if (-not (Test-Path -LiteralPath infra\terraform.tfvars)) {
  Copy-Item -LiteralPath infra\terraform.tfvars.example -Destination infra\terraform.tfvars
}
# Edit infra\terraform.tfvars: set subscription_id to your actual subscription ID.
terraform -chdir=infra init
terraform -chdir=infra plan
terraform -chdir=infra apply
```

For Sweden Central, set `location = "swedencentral"` in the variables file. Terraform **creates and owns a new resource group**; select a new name, not one containing existing resources. The generated gateway name is deterministic; override `gateway_name` if it collides. The gateway uses a system-assigned identity and public backend routing; private Foundry endpoints require a different networking design **before** gateway creation. The template outputs the resource ID, principal ID, and hostname. It does **not** create an APIM subscription key, APIM policy, or Foundry model automatically. Store Terraform state securely; the local `.tfstate` and `.tfvars` files are ignored by Git.

## Connect your Foundry account

1. Open [AI Gateway portal](https://ai.gateway.azure.com), select the new gateway, then **Models > Add > Models from Microsoft Foundry**. Select the subscription and your *existing Foundry account* (not a single deployment).
2. Select **Managed identity > System-assigned managed identity** and leave **Create an alias for each model** selected. The portal grants the gateway identity **Foundry User** on the account. Confirm that the imported model supports Chat Completions and copy its **callable alias**.
3. Go to **Access > Keys > Create API key**. Store the gateway-wide runtime key securely; it is **not** a Foundry key or an APIM subscription key.
4. Fill in the three settings described in **Run the labs**, then launch Jupyter. No Foundry credentials are needed in the notebook environment; the gateway uses its configured backend authentication.

## Configure a two-model pool

In the [AI Gateway portal](https://ai.gateway.azure.com), configure an alias targeting **two registered models** that support Chat Completions. Set both targets to equal priority and equal weight, and ensure the gateway is authorized to call each backend. Prefer distinguishable model versions so the reported model IDs can help distinguish responses.

Set the shared `AI_GATEWAY_MODEL_ALIAS` in `.env` to that pooled alias. If you already have a compatible pool, reuse it; no separate pool environment variable or backend IDs are required by the notebooks. Only the opt-in governance cells change a policy; the notebooks do not create or change aliases or telemetry destinations.

Optionally inspect alias targets from PowerShell using the gateway resource ID output by Terraform:

```powershell
$gatewayId = terraform -chdir=infra output -raw gateway_id
az rest --method get --url "https://management.azure.com$gatewayId/workspaces/default/aliases?api-version=2026-09-01-preview" --query "value[].{alias:properties.aliasName,targets:properties.backendModels,endpoints:properties.gatewayEndpoints}" -o json
```

Use a management API version supported by your gateway; the example matches the preview resource version in this Terraform template. Prefer the portal for pool setup rather than mixing management schemas from different API versions.

Equal priority and equal weight request a 1:1 split over time, not an alternating or guaranteed split in any small sample. The notebook makes up to six sequential calls using the **same alias** and repeated long prompt, displays each answer, reported model ID, and prompt and cached token counts, and summarizes observed IDs. Different wording alone does *not* prove which backend answered; if response `model` is absent, identical, or rewritten, use the gateway's per-backend telemetry to verify routing. A policy or provider quota may interrupt the sample.

[Azure OpenAI prompt caching](https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching) is enabled by default on supported models. It requires at least 1,024 input tokens and an identical initial 1,024-token prefix. A positive `cached_tokens` value in the response indicates a hit; zero is a miss, and `not reported` means the field was absent. Pool targets may each need a warmup, and a hit is not guaranteed. To isolate caching from pool routing, temporarily set the environment to a separate non-production single-model alias and rerun the notebook setup cell; that test does not verify pooling.

## Before publishing

1. Keep runtime keys in `.env`, not notebooks, examples, logs, or source. The ignore rules cover local environment files, common private-key files, Terraform state, variable files, saved plans, crash logs, and notebook checkpoints. Custom filenames may need additional rules. Git ignores do **not** remove files already tracked, prevent force-adds, or erase earlier commits.
2. Clear and save notebook outputs before staging. Outputs and tracebacks can contain prompts, responses, endpoints, or credentials. In Jupyter use **Clear All Outputs**, or from the repository root run:

   ```powershell
   python -m jupyter nbconvert --clear-output --inplace notebooks\01-model-access.ipynb notebooks\02-governance.ipynb notebooks\03-model-pool.ipynb
   ```

3. Run `python -m unittest discover -s tests -v`. The tests check that notebooks contain no saved output and that common local secret files are ignored. They are not a comprehensive secret scanner.
4. Once the directory is a Git repository, inspect `git status --short --untracked-files=all`, `git ls-files --cached --ignored --exclude-standard`, and the staged diff with `git diff --cached`. Investigate any tracked ignored files before publishing. Run a local secret scanner over the files and Git history, and enable hosting-provider secret scanning and push protection where available.
5. If a credential was ever committed or exposed, revoke or rotate it; deleting it from the latest files does not remove it from Git history. Review and clean the history before making it public. Scanners and ignore rules reduce risk but cannot guarantee that every secret is caught.

## Concepts and scope

| AI Gateway preview | Traditional API Management service |
| --- | --- |
| Model providers, registered models, **stable callable aliases with weighted model targets**, federated MCP servers, tool catalog | APIs, operations, products, subscriptions, backend pools; AI-specific policies are also available |
| Gateway runtime API key or Entra runtime auth; managed identity to Foundry | APIM subscription keys or other configured API auth; separately configured backend auth |
| AI-focused consumption and estimated-spend controls, model/tool telemetry | General API policies plus AI policies; often requires explicit API/policy setup |

These services use different management models; traditional APIM also supports AI-specific policies and governance.

A stable model alias separates the name used by an application from its backend target(s). Changes to those targets must preserve API compatibility and account for other applications using the alias. Gateway caller authentication is separate from authentication between the gateway and each backend.

Consumption limits must be configured before running the optional limit exercise. Only the opt-in governance cells modify a policy; the notebooks do not create sustained load. Configure a telemetry destination before collecting traffic if you need to inspect usage, latency, or backend routing. Estimated spend is not a bill; use Foundry billing for actual charges.

Federated MCP tools require an existing tool backend and are outside the scope of these notebooks.

References: [AI Gateway quickstart](https://learn.microsoft.com/azure/ai-gateway/quickstart-create-ai-gateway), [resource schema](https://learn.microsoft.com/azure/templates/microsoft.apimanagement/2026-09-01-preview/aigateways), [alias backend models API](https://learn.microsoft.com/en-us/rest/api/aigateway/ai-gateway-alias-resources/create-or-update?view=rest-aigateway-2026-11-01), [AI Gateway vs APIM](https://learn.microsoft.com/azure/ai-gateway/compare-api-management-service), [concepts](https://learn.microsoft.com/azure/ai-gateway/concepts).
