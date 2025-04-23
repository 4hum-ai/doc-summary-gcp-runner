variable "authorized_query_users" {
  description = "List of email addresses authorized to run BigQuery jobs"
  type        = list(string)
  default     = []
}

# Grant bigquery.jobUser role to authorized users
resource "google_project_iam_member" "bigquery_job_users" {
  for_each = toset(var.authorized_query_users)
  project  = "tasco-auto-457703" # or use var.project_id if you have that variable defined
  role     = "roles/bigquery.jobUser"
  member   = "user:${each.value}"
}

# Optionally, grant data viewer access to specific datasets
resource "google_bigquery_dataset_iam_member" "dataset_viewers" {
  for_each   = toset(var.authorized_query_users)
  project    = "tasco-auto-457703"
  dataset_id = "dms_122739" # Your dataset ID
  role       = "roles/bigquery.dataViewer"
  member     = "user:${each.value}"
}