variable "project" {
  description = "The Google Cloud project."
  type        = string
}

variable "region" {
  description = "Where the bucket is, such as europe-north1."
  type        = string
}

variable "bucket" {
  description = "The bucket's name: pg-backup.json's wal.bucket."
  type        = string
}

variable "prefix" {
  description = "WALG_GS_PREFIX's path in the bucket: pg-backup.json's wal.prefix. The lifecycle rules delete under it."
  type        = string
}

variable "account_id" {
  description = "The account WAL-G writes as: pg-backup.json's wal.account, before the @."
  type        = string
}

variable "account_display_name" {
  description = "The account's display name."
  type        = string
}

variable "account_description" {
  description = "The account's description."
  type        = string
}

variable "role_id" {
  description = "The custom role for deleting upload chunks. A custom role is the project's, so each archive in a project names its own."
  type        = string
}

variable "wal_days" {
  description = "Days a WAL segment is kept: a day longer than base_days, so the oldest base backup can still replay."
  type        = number
  default     = 16
}

variable "base_days" {
  description = "Days a base backup is kept. A restore reaches back base_days - 1 days, to any moment."
  type        = number
  default     = 15
}

variable "labels" {
  description = "The bucket's labels."
  type        = map(string)
  default     = { managed-by = "terraform" }
}
