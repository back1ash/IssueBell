variable "tenancy_ocid" {
  description = "OCI tenancy OCID; the bucket and policy are created in the tenancy root."
  type        = string
}

variable "user_ocid" {
  description = "OCI API signing user OCID used by the backup uploader."
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint for the OCI API signing key."
  type        = string
  sensitive   = true
}

variable "private_key_path" {
  description = "Path to the OCI API signing private key on the Terraform runner."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "OCI region that hosts the bucket."
  type        = string
  default     = "ap-seoul-1"
}

variable "bucket_name" {
  description = "Globally unique Object Storage bucket name within the tenancy namespace."
  type        = string
  default     = "issuebell-db-backups"
}

variable "retention_days" {
  description = "Number of days to retain IssueBell database backup objects."
  type        = number
  default     = 7

  validation {
    condition     = var.retention_days >= 1 && var.retention_days <= 365
    error_message = "retention_days must be between 1 and 365."
  }
}
