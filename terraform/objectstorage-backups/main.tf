data "oci_objectstorage_namespace" "tenancy" {
  compartment_id = var.tenancy_ocid
}

resource "oci_objectstorage_bucket" "issuebell_backups" {
  compartment_id = var.tenancy_ocid
  namespace      = data.oci_objectstorage_namespace.tenancy.namespace
  name           = var.bucket_name

  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  object_events_enabled = false
  versioning            = "Disabled"
  auto_tiering          = "Disabled"

  freeform_tags = {
    application = "issuebell"
    environment = "production"
    managed_by  = "terraform"
    purpose     = "postgresql-backups"
  }
}

resource "oci_objectstorage_object_lifecycle_policy" "issuebell_backups" {
  bucket    = oci_objectstorage_bucket.issuebell_backups.name
  namespace = data.oci_objectstorage_namespace.tenancy.namespace

  rules {
    action      = "DELETE"
    is_enabled  = true
    name        = "delete-expired-issuebell-postgres-backups"
    target      = "objects"
    time_amount = tostring(var.retention_days)
    time_unit   = "DAYS"

    object_name_filter {
      inclusion_prefixes = ["issuebell/postgresql/"]
    }
  }

  rules {
    action      = "ABORT"
    is_enabled  = true
    name        = "abort-incomplete-multipart-uploads"
    target      = "multipart-uploads"
    time_amount = "1"
    time_unit   = "DAYS"
  }
}

resource "oci_identity_policy" "issuebell_backup_uploader" {
  compartment_id = var.tenancy_ocid
  name           = "IssueBellProductionBackupObjectWrite"
  description    = "Allow the IssueBell backup API user to upload and verify objects only under the production PostgreSQL backup prefix."

  statements = [
    "Allow any-user to manage objects in tenancy where all {target.bucket.name = '${var.bucket_name}', target.object.name = 'issuebell/postgresql/*', request.user.id = '${var.user_ocid}', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_OVERWRITE', request.permission = 'OBJECT_INSPECT'}}",
    "Allow service objectstorage-${var.region} to manage object-family in tenancy where target.bucket.name = '${var.bucket_name}'"
  ]

  freeform_tags = {
    application = "issuebell"
    environment = "production"
    managed_by  = "terraform"
  }
}
