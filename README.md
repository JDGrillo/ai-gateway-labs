# AI Gateway preview demo

This deploys the **dedicated AI Gateway** (`Microsoft.ApiManagement/aigateways`), not a traditional API Management service (`Microsoft.ApiManagement/service`). Terraform uses AzureRM for the resource group and AzAPI for the preview gateway resource. It reuses your existing Microsoft Foundry account and model deployments; it does not provision models or a second APIM service. AI Gateway is a public preview, currently available in **East US 2** and **Sweden Central**. The template uses the provider's default SKU rather than assuming an undocumented SKU name or price; check the portal pricing before deployment.

## Run the labs

Use an existing dedicated AI Gateway with an imported Chat Completions model, or provision one using the sections below. The generic [environment template](.env.example) contains only **three shared settings**. Keep deployment-specific values and credentials in the root `.env`, which is ignored by Git.

| Setting | Where to find it in the [AI Gateway portal](https://ai.gateway.azure.com) |
| --- | --- |
| `AI_GATEWAY_CHAT_URL` | Open the model's **Overview** and copy the full **Chat completions** HTTPS endpoint. Do not construct it from the hostname. |
| `AI_GATEWAY_API_KEY` | **Access > Keys > Create API key**. Use a gateway runtime key, not a Foundry key or APIM subscription key. Runtime keys grant gateway-wide access; protect and revoke them when no longer needed. |
| `AI_GATEWAY_MODEL_ALIAS` | **Models > Aliases**: copy the callable alias (`aliasName`), not its ARM resource name, resource ID, or backend deployment name. |

All three notebooks use these same settings. The alias can target one model or a pool; notebook 3 requires a pool with two compatible chat targets. Subscription IDs, resource groups, provider IDs, backend resource IDs, and other protocol endpoints are not needed in the notebook environment. Manage those through Terraform or the portal instead. Embeddings and Anthropic Messages models cannot substitute for Chat Completions targets in these labs.

From the repository root:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
  Copy-Item -LiteralPath .env.example -Destination .env
}
# Edit .env and fill in the three settings from the portal.
python -m pip install -r requirements.txt
python -m jupyter lab
```

The notebooks load the root `.env` when started from the repository root or `notebooks` directory. Existing shell variables take precedence, so remove stale shell values or update them before starting Jupyter. After editing `.env`, restart the notebook kernel so it reloads the file; restart Jupyter itself after changing shell variables. Missing required settings fail explicitly. Never save real keys or request headers in notebook output.

## Suggested demo

Use a dedicated runtime key and demo-only alias, and show configuration in the portal without revealing credentials. A dedicated key is still gateway-wide, not restricted to that alias. One run of all three notebooks makes at most **16 model requests** and incurs usage charges; rerunning cells makes additional requests. Do **not** reprovision an existing gateway just to run the lab.

| Step | Show | Expected result and caveat |
| --- | --- | --- |
| 1. [Model access](notebooks/01-model-access.ipynb) | In the portal, show the model alias and backend target(s); run the notebook and point out the gateway host, alias, answer, and any reported token usage. | One request uses a gateway runtime key and the callable alias, not a Foundry credential. A response alone does not prove which backend answered. |
| 2. [Governance](notebooks/02-governance.ipynb) | Run the notebook and show each request's status and reported token counts; compare them with gateway policy and telemetry. | At most three requests. A preconfigured demo consumption limit may cause a `429`; without one, all may succeed. A `429` can also come from backend quota, so check its source. |
| 3. [Model pool](notebooks/03-model-pool.ipynb) | First configure the alias with two compatible chat targets as described below. Run the notebook and show the reported model IDs and counts, then inspect per-backend telemetry. | At most 12 requests. Equal weights do not guarantee an even split in a small sample; response text or a reported model ID alone is not definitive backend-routing evidence. |

Use the same alias for all three notebooks; it may be a pool. To compare a pool with a single model in notebooks 1 and 2, choose a single-model chat alias and change `AI_GATEWAY_MODEL_ALIAS`. Changing a shared alias or consumption policy can affect other applications; use isolated demo configuration.

Local regression checks (mocked requests, no Azure traffic or keys required):

```powershell
python -m unittest discover -s tests -v
```

## Provision a new gateway (optional)

Prerequisites: Terraform 1.5+, Azure CLI (authenticated with `az login`), a subscription with Contributor access to create the demo resource group, and the `Microsoft.ApiManagement` provider registered. Your existing Foundry account must have a publicly accessible endpoint and at least one deployment supporting Chat Completions. For model import you also need Reader and User Access Administrator (or equivalent) on that account.

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

## Two-model pool demo

In the [AI Gateway portal](https://ai.gateway.azure.com), configure an alias targeting **two registered models** that support Chat Completions. Set both targets to equal priority and equal weight, and ensure the gateway is authorized to call each backend. Prefer distinguishable model versions so the reported model IDs can help demonstrate routing.

Set the shared `AI_GATEWAY_MODEL_ALIAS` in `.env` to that pooled alias. If you already have a compatible pool, reuse it; no separate pool environment variable or backend IDs are required by the notebooks. The notebooks do not create or change Azure aliases, policies, limits, or telemetry destinations.

Optionally inspect alias targets from PowerShell using the gateway resource ID output by Terraform:

```powershell
$gatewayId = terraform -chdir=infra output -raw gateway_id
az rest --method get --url "https://management.azure.com$gatewayId/workspaces/default/aliases?api-version=2026-09-01-preview" --query "value[].{alias:properties.aliasName,targets:properties.backendModels,endpoints:properties.gatewayEndpoints}" -o json
```

Use a management API version supported by your gateway; the example matches the preview resource version in this Terraform template. Prefer the portal for pool setup rather than mixing management schemas from different API versions.

Equal priority and equal weight request a 1:1 split over time, not an alternating or guaranteed split in any small sample. The notebook makes 12 short sequential calls using the **same alias**, displays each answer and any provider-reported model ID, and summarizes observed IDs. Different wording alone does *not* prove which backend answered; if response `model` is absent, identical, or rewritten, use the gateway's per-backend telemetry to verify routing. A policy or provider quota may interrupt the sample.

## Before publishing

1. Keep runtime keys in `.env`, not notebooks, examples, logs, or source. The ignore rules cover local environment files, common private-key files, Terraform state, variable files, saved plans, crash logs, and notebook checkpoints. Custom filenames may need additional rules. Git ignores do **not** remove files already tracked, prevent force-adds, or erase earlier commits.
2. Clear and save notebook outputs before staging. Outputs and tracebacks can contain prompts, responses, endpoints, or credentials. In Jupyter use **Clear All Outputs**, or from the repository root run:

   ```powershell
   python -m jupyter nbconvert --clear-output --inplace notebooks\01-model-access.ipynb notebooks\02-governance.ipynb notebooks\03-model-pool.ipynb
   ```

3. Run `python -m unittest discover -s tests -v`. The tests check that notebooks contain no saved output and that common local secret files are ignored. They are not a comprehensive secret scanner.
4. Once the directory is a Git repository, inspect `git status --short --untracked-files=all`, `git ls-files --cached --ignored --exclude-standard`, and the staged diff with `git diff --cached`. Investigate any tracked ignored files before publishing. Run a local secret scanner over the files and Git history, and enable hosting-provider secret scanning and push protection where available.
5. If a credential was ever committed or exposed, revoke or rotate it; deleting it from the latest files does not remove it from Git history. Review and clean the history before making it public. Scanners and ignore rules reduce risk but cannot guarantee that every secret is caught.

## Demo talking points

| AI Gateway preview | Traditional API Management service |
| --- | --- |
| Model providers, registered models, **stable callable aliases with weighted model targets**, federated MCP servers, tool catalog | APIs, operations, products, subscriptions, backend pools; AI-specific policies are also available |
| Gateway runtime API key or Entra runtime auth; managed identity to Foundry | APIM subscription keys or other configured API auth; separately configured backend auth |
| AI-focused consumption and estimated-spend controls, model/tool telemetry | General API policies plus AI policies; often requires explicit API/policy setup |

These are different management models, **not** a claim that traditional APIM cannot implement AI governance. To demo limits, configure a small **model consumption limit** for the test caller in the [AI Gateway portal](https://ai.gateway.azure.com/docs) first; the notebook never changes production policies or creates sustained load. To demo a stable alias, change its target to another compatible model in the portal and rerun the same notebook request. To inspect usage/latency, configure a telemetry destination in the portal before collecting traffic. Estimated spend is not a bill; use Foundry billing for actual charges. Federated MCP tools require an existing tool backend and are not provisioned here.

References: [AI Gateway quickstart](https://learn.microsoft.com/azure/ai-gateway/quickstart-create-ai-gateway), [resource schema](https://learn.microsoft.com/azure/templates/microsoft.apimanagement/2026-09-01-preview/aigateways), [alias backend models API](https://learn.microsoft.com/en-us/rest/api/aigateway/ai-gateway-alias-resources/create-or-update?view=rest-aigateway-2026-11-01), [AI Gateway vs APIM](https://learn.microsoft.com/azure/ai-gateway/compare-api-management-service), [concepts](https://learn.microsoft.com/azure/ai-gateway/concepts).
