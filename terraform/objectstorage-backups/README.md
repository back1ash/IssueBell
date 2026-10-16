# IssueBell production database backups

This isolated Terraform root manages only the IssueBell backup bucket, its lifecycle rules, and the uploader IAM policy. It intentionally does not share the compute/network Terraform state.

## Resources

- Private Standard tier bucket `issuebell-db-backups` in the tenancy root compartment.
- OCI-managed encryption at rest, with public access disabled and object versioning disabled.
- Delete objects under `issuebell/postgresql/` after 7 days; abort incomplete multipart uploads after 1 day.
- A tenancy policy limited to the configured OCI API user, this bucket, the PostgreSQL backup object prefix, and create/overwrite/inspect permissions. It also grants the region's Object Storage service the standard lifecycle-management access, scoped to this bucket only.

The 7-day retention is the configured production retention policy. OCI lifecycle execution is asynchronous, so an object may remain billable briefly after it reaches the expiration age.

## Apply

Run Terraform in this directory and pass the existing OCI credentials file as a variable file. Do not commit the variable file or Terraform state.

```sh
terraform init
terraform plan -var-file=/home/haejun/workspace/OCI-free/terraform/terraform.tfvars
terraform apply -var-file=/home/haejun/workspace/OCI-free/terraform/terraform.tfvars
```

The existing `terraform.tfvars` must define `tenancy_ocid`, `user_ocid`, `fingerprint`, `private_key_path`, and `region`. The API signing user identified by `user_ocid` is the same principal that must be used by the Kubernetes OCI CLI config secret.
