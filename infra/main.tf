# ngpvan-mcp on Cloud Run, with the VAN key and the MCP bearer token in
# Secret Manager. Validated only; never applied.
#
# Secrets are created empty. Add their values outside Terraform so they
# never sit in state or in this repo:
#   printf '%s' "$VAN_API_KEY" | gcloud secrets versions add ngpvan-mcp-van-api-key --data-file=-
#   openssl rand -hex 32 | gcloud secrets versions add ngpvan-mcp-http-token --data-file=-

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "Google Cloud project to deploy into"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  type        = string
  description = "Container image built from the Dockerfile, e.g. us-central1-docker.pkg.dev/PROJECT/ngpvan-mcp/server:0.1.0"
}

variable "van_app_name" {
  type        = string
  description = "The Application Name NGP VAN issued with the API key"
}

variable "invoker_members" {
  type        = list(string)
  default     = []
  description = "Who may call the service, e.g. [\"serviceAccount:agent@PROJECT.iam.gserviceaccount.com\"]. Never allUsers."

  validation {
    condition     = !contains(var.invoker_members, "allUsers") && !contains(var.invoker_members, "allAuthenticatedUsers")
    error_message = "The service holds an organization's VAN key; it can't be public."
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(["run.googleapis.com", "secretmanager.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "server" {
  account_id   = "ngpvan-mcp"
  display_name = "ngpvan-mcp server"
}

resource "google_secret_manager_secret" "van_api_key" {
  secret_id = "ngpvan-mcp-van-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "http_token" {
  secret_id = "ngpvan-mcp-http-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# The server's own identity can read its two secrets and nothing else.
resource "google_secret_manager_secret_iam_member" "server_reads" {
  for_each = {
    van  = google_secret_manager_secret.van_api_key.id
    http = google_secret_manager_secret.http_token.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.server.email}"
}

resource "google_cloud_run_v2_service" "server" {
  name     = "ngpvan-mcp"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.server.email

    # VAN rate limits are per key, so one instance keeps the client-side
    # limiter accurate.
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      env {
        name  = "VAN_APP_NAME"
        value = var.van_app_name
      }
      env {
        name = "VAN_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.van_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "NGPVAN_MCP_HTTP_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.http_token.secret_id
            version = "latest"
          }
        }
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.server_reads]
}

# Calls need a Google identity with run.invoker on top of the bearer token.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)
  name     = google_cloud_run_v2_service.server.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

output "url" {
  value = "${google_cloud_run_v2_service.server.uri}/mcp"
}
