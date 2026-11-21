# Deploying ngpvan-mcp on Google Cloud Run

`main.tf` describes one Cloud Run service running the server over streamable HTTP, with the VAN API
key and the MCP bearer token in Secret Manager. It has been checked with `terraform validate` and
`terraform fmt` only. It has not been planned against a real project or applied.

## What it creates

- A service account for the server, allowed to read its two secrets and nothing else.
- Two empty secrets: `ngpvan-mcp-van-api-key` and `ngpvan-mcp-http-token`. Add their values
  with `gcloud secrets versions add` so they never pass through Terraform state.
- The Cloud Run service, at most one instance (VAN rate limits are per key, so one instance keeps
  the client-side limiter honest), scaling to zero when idle.
- `roles/run.invoker` for the identities you list in `invoker_members`. The variable refuses
  `allUsers`.

## Two locks on the door

A caller needs both a Google identity with `run.invoker` and the bearer token. Cloud Run and the
server both read an `Authorization` header, so the Google ID token goes in
`X-Serverless-Authorization` (Cloud Run checks it there and passes `Authorization` through), and
the server's token goes in `Authorization: Bearer ...`.

## Audit log

`deploy/cloudrun.toml` sets the audit path to `stderr`, which Cloud Run sends to Cloud Logging.
Each tool call is one JSON line there, with the same fields as the local file.

## Steps

```bash
gcloud artifacts repositories create ngpvan-mcp --repository-format=docker --location=us-central1
docker build -t us-central1-docker.pkg.dev/PROJECT/ngpvan-mcp/server:0.1.0 .
docker push us-central1-docker.pkg.dev/PROJECT/ngpvan-mcp/server:0.1.0

cd infra
terraform init
terraform plan -var project_id=PROJECT -var van_app_name=yourAppName \
  -var image=us-central1-docker.pkg.dev/PROJECT/ngpvan-mcp/server:0.1.0
```

Cost at low use is close to nothing: Cloud Run scales to zero, and Secret Manager charges cents per
secret version per month.
