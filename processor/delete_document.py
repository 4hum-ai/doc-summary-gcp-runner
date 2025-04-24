from google.cloud import bigquery

def delete_document(
    event_id: str,
    input_bucket: str,
    filename: str,
    project: str,
    bq_dataset: str,
    bq_table: str,
) -> None:
    """Delete a document summary from BigQuery when the source document is deleted.

    Args:
        event_id: The Eventarc trigger event ID.
        input_bucket: Name of the input bucket.
        filename: Name of the input file.
        project: Google Cloud project ID.
        bq_dataset: Name of the BigQuery dataset.
        bq_table: Name of the BigQuery table.
    """
    doc_path = f"gs://{input_bucket}/{filename}"
    print(f"🗑️ {event_id}: Removing document summary from BigQuery: {project}.{bq_dataset}.{bq_table}")

    bq_client = bigquery.Client(project=project)
    query = f"""
    DELETE FROM `{project}.{bq_dataset}.{bq_table}`
    WHERE 'document_path' = '{doc_path}'
    """

    query_job = bq_client.query(query)
    query_job.result()  # Wait for the query to complete

    print(f"✅ Deleted: {doc_path}")