output "bucket" {
  description = "The bucket's name."
  value       = google_storage_bucket.wal.name
}

output "account" {
  description = "The account's email: pg-backup.json's wal.account."
  value       = google_service_account.wal.email
}
