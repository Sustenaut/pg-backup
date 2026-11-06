# A PostgreSQL database's write-ahead log archive: a bucket WAL-G uploads every finished segment
# and each night's base backup to, and the account it uploads as.
#
# The account may create and read objects, never replace one: WAL-G reads to see whether a
# segment is already there (WALG_PREVENT_WAL_OVERWRITE) before it writes, as PostgreSQL may ask
# it to archive one again. It may delete only the pieces of an upload: WAL-G sends each file to
# Cloud Storage in chunks under "<file>_chunks/", composes them into the file and then deletes
# the chunks, and an upload whose chunks it can't delete never completes. Every name without
# "_chunks/", the segments and backups themselves, it can't touch. The bucket deletes those by
# age. (A retention policy can't be used: it would block the chunk deletes too.)
#
# Everything in it is encrypted by WAL-G before it leaves the server, so the bucket alone
# reveals nothing. No access logging: the only writer is the add-only account below, and Cloud
# Audit Logs' admin activity, which is always on, records any change to the bucket or to who
# may use it.
resource "google_storage_bucket" "wal" { # NOSONAR: the reasons are the comment above
  project                     = var.project
  name                        = var.bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age            = var.wal_days
      matches_prefix = ["${var.prefix}/wal_005/"]
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      age            = var.base_days
      matches_prefix = ["${var.prefix}/basebackups_005/"]
    }
    action {
      type = "Delete"
    }
  }

  labels = var.labels
}

resource "google_service_account" "wal" {
  project      = var.project
  account_id   = var.account_id
  display_name = var.account_display_name
  description  = var.account_description
}

resource "google_storage_bucket_iam_member" "wal" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])

  bucket = google_storage_bucket.wal.name
  role   = each.value
  member = google_service_account.wal.member
}

# WAL-G's chunk clean-up, and nothing else: one permission, on names with "_chunks/" in them.
resource "google_project_iam_custom_role" "wal_chunk_delete" {
  project     = var.project
  role_id     = var.role_id
  title       = "WAL-G chunk delete"
  description = "Deletes objects; granted only with a condition on the object name."
  permissions = ["storage.objects.delete"]
}

# A new custom role takes a little while before a grant may name it ("does not exist in the
# resource's hierarchy"), so a first apply waits for it instead of failing once.
resource "time_sleep" "wal_chunk_delete_role" {
  create_duration = "30s"
  triggers        = { role = google_project_iam_custom_role.wal_chunk_delete.id }
}

resource "google_storage_bucket_iam_member" "wal_chunk_delete" {
  bucket = google_storage_bucket.wal.name
  role   = time_sleep.wal_chunk_delete_role.triggers.role
  member = google_service_account.wal.member

  condition {
    title       = "upload-chunks-only"
    description = "Only the chunks WAL-G composes an upload from."
    expression  = "resource.name.extract('projects/_/buckets/${google_storage_bucket.wal.name}/objects/{file}_chunks/') != ''"
  }
}
