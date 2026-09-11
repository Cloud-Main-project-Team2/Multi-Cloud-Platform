variable "project_id" {
  description = "GCP project ID (cloud_accounts.external_account_id)"
  type        = string
}

variable "region" {
  description = "GCP region, e.g. asia-northeast3"
  type        = string
}

variable "zone" {
  description = "GCP zone, e.g. asia-northeast3-a"
  type        = string
}

variable "machine_type" {
  description = "GCP machine type, e.g. e2-micro"
  type        = string
}

variable "instance_name" {
  description = "Compute Engine instance name (also used to derive the per-job firewall rule name)"
  type        = string
}

variable "labels" {
  description = "Labels applied to the instance"
  type        = map(string)
  default     = {}
}
