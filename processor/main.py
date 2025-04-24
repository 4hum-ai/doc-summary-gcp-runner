# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import json
import sys
import base64
from collections.abc import Iterator
from datetime import datetime
import functions_framework
from flask import Request, Response
from google import genai
from google.genai.types import GenerateContentConfig
from google.api_core.client_options import ClientOptions
from google.cloud import documentai
from google.cloud import bigquery
from google.cloud import storage
import urllib.parse
from . import delete_document

@functions_framework.http
def handle_task(request: Request) -> Response:
    """HTTP Cloud Function triggered by Cloud Tasks to process a document.

    Args:
        request (flask.Request): The HTTP request object.
            The body is expected to be a JSON payload from the webhook function.

    Returns:
        A Flask Response object.
    """
    event_id = "unknown"
    try:
        # --- Configuration --- Requires Environment Variables ---
        project = os.environ["PROJECT_ID"]
        docai_processor_id = os.environ["DOCAI_PROCESSOR"]
        docai_location = os.environ.get("DOCAI_LOCATION", "us")
        output_bucket = os.environ["OUTPUT_BUCKET"] 
        bq_dataset = os.environ["BQ_DATASET"]
        bq_table = os.environ["BQ_TABLE"]
        # --- End Configuration ---

        if request.method != 'POST':
            logging.warning("Received non-POST request.")
            return Response("Method Not Allowed", status=405)
        
        payload = request.get_json(silent=True)
        if not payload:
            logging.error("Missing JSON Payload")
            return Response("Bad Request: Missing JSON Payload", status=200)      

        return process_request(payload)

    except Exception as e:
        logging.error(f"{event_id}: Error processing task: {str(e)}")
        return Response(f"Error: {str(e)}", status=200)  # Return 200 OK to prevent Cloud Tasks from retrying

def process_request(payload):
    """Process the parsed payload and handle the request accordingly."""
    event_id = payload.get("event_id", "N/A")
    event_type = payload.get("event_type")
    input_bucket = payload.get("bucket")
    filename = payload.get("filename")
    uploader = payload.get("uploader", "unknown")  # Get uploader from payload

    print(f"🔲process_request: {payload}")

    # --- Basic Validation --- #
    if not all([event_type, input_bucket, filename]):
        print(f"{event_id}: Task payload missing required fields (event_type, bucket, filename). Payload: {payload}")
        return Response("Bad Request: Missing required fields", status=200)

    # --- Route to appropriate handler --- #
    if event_type == "google.cloud.storage.object.v1.finalized":
        mime_type = payload.get("content_type")
        time_created_str = payload.get("time_created")
        if not mime_type or not time_created_str:
            print(f"{event_id}: Task payload for finalized event missing content_type or time_created. Payload: {payload}")
            return Response("Bad Request: Missing fields for finalized event", status=200)
        # Process the document
        process_document(
            event_id=payload["event_id"],
            input_bucket=payload["bucket"],
            filename=payload["filename"],
            mime_type=payload["content_type"],
            time_uploaded=datetime.fromisoformat(payload["time_created"]),
            uploader=uploader,  # Pass uploader to process_document
            project=os.environ["PROJECT_ID"],
            location=os.environ["LOCATION"],
            docai_processor_id=os.environ["DOCAI_PROCESSOR"],
            docai_location=os.environ.get("DOCAI_LOCATION", "us"),
            output_bucket=os.environ["OUTPUT_BUCKET"],
            bq_dataset=os.environ["BQ_DATASET"],
            bq_table=os.environ["BQ_TABLE"],
        )
        # Return success response
        return Response("Document processing started", status=200)
        
    elif event_type == "google.cloud.storage.object.v1.deleted":
        # Handle deletion event if needed
        delete_document(
            event_id=event_id,
            input_bucket=input_bucket,
            filename=filename,
            project=os.environ["PROJECT_ID"],
            bq_dataset=os.environ["BQ_DATASET"],
            bq_table=os.environ["BQ_TABLE"],
        )
        return Response("Document deletion acknowledged", status=200)
        
    else:
        print(f"{event_id}: Unsupported event type: {event_type}")
        return Response(f"Unsupported event type: {event_type}", status=200)

# This is required for Cloud Functions (2nd gen) to listen on port 8080
if __name__ == "__main__":
    # Get port from environment variable or default to 8080
    port = int(os.getenv("PORT", "8080"))
    functions_framework.start(target="handle_task", port=port)

# ================================================
# Document Processing and Deletion Logic
# ================================================

def process_document(
    event_id: str,
    input_bucket: str,
    filename: str,
    mime_type: str,
    time_uploaded: datetime,
    uploader: str,
    project: str,
    location: str,
    docai_processor_id: str,
    docai_location: str,
    output_bucket: str,
    bq_dataset: str,
    bq_table: str,
):
    """Process a new document.

    Args:
        event_id: ID of the event.
        input_bucket: Name of the input bucket.
        filename: Name of the input file.
        mime_type: MIME type of the input file.
        time_uploaded: Time the input file was uploaded.
        uploader: Identity of the user who uploaded the file.
        project: Google Cloud project ID.
        location: Google Cloud location.
        docai_processor_id: ID of the Document AI processor.
        docai_location: Location of the Document AI processor.
        output_bucket: Name of the output bucket.
        bq_dataset: Name of the BigQuery dataset.
        bq_table: Name of the BigQuery table.
    """
    doc_path = f"gs://{input_bucket}/{filename}"
    auth_url = f"https://storage.cloud.google.com/{urllib.parse.quote(input_bucket)}/{urllib.parse.quote(filename)}"
    
    # Parse folder structure
    path_parts = filename.split('/')
    file_name = path_parts[-1]  # Last part is the file name
    parent_folders = path_parts[:-1]  # All parts except the last are parent folders
    
    # Get file size from GCS
    storage_client = storage.Client()
    bucket = storage_client.bucket(input_bucket)
    blob = bucket.blob(filename)
    file_size = blob.size  # Size in bytes
    
    print(f"(1/3) Getting document text")
    print(f"  - Folder path:    {'/'.join(parent_folders)}")
    print(f"  - File name:      {file_name}")
    doc_text = "\n".join(
        get_document_text(
            doc_path,
            mime_type,
            docai_processor_id,
            output_bucket,
            docai_location,
        )
    )
    text_length = len(doc_text)  # Text length in characters

    print(f"(2/3): Summarizing document")
    print(f"  - File size:      {file_size} bytes")
    print(f"  - Text length:    {text_length} characters")
    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=doc_text,
        config=GenerateContentConfig(
            system_instruction=[
                "Generate abstract, in the same language. Just return the abstract and nothing more."
            ]
        ),
    )
    doc_abstract = response.text

    print(f"  - Summary length: {len(doc_abstract)} characters")

    print(f"(3/3) Writing document to BigQuery: {project}.{bq_dataset}.{bq_table}")
    write_to_bigquery(
        event_id=event_id,
        time_uploaded=time_uploaded,
        doc_path=doc_path,
        auth_url=auth_url,
        doc_text=doc_text,
        doc_abstract=doc_abstract,
        uploader=uploader,
        file_size=file_size,
        text_length=text_length,
        parent_folders=parent_folders,
        file_name=file_name,
        project=project,
        bq_dataset=bq_dataset,
        bq_table=bq_table,
    )

    print(f"✅ Completed: {event_id}")


def get_document_text(
    input_file: str,
    mime_type: str,
    processor_id: str,
    temp_bucket: str,
    docai_location: str = "us",
) -> Iterator[str]:
    """Perform Optical Character Recognition (OCR) with Document AI on a Cloud Storage file.

    For more information, see:
        https://cloud.google.com/document-ai/docs/process-documents-ocr

    Args:
        input_file: GCS URI of the document file.
        mime_type: MIME type of the document file.
        processor_id: ID of the Document AI processor.
        temp_bucket: GCS bucket to store Document AI temporary files.
        docai_location: Location of the Document AI processor.

    Yields: The document text chunks.
    """
    # You must set the `api_endpoint` if you use a location other than "us".
    documentai_client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{docai_location}-documentai.googleapis.com")
    )

    # We're using batch_process_documents instead of process_document because
    # process_document has a quota limit of 15 pages per document, while
    # batch_process_documents has a quota limit of 500 pages per request.
    #   https://cloud.google.com/document-ai/quotas#general_processors
    operation = documentai_client.batch_process_documents(
        request=documentai.BatchProcessRequest(
            name=processor_id,
            input_documents=documentai.BatchDocumentsInputConfig(
                gcs_documents=documentai.GcsDocuments(
                    documents=[
                        documentai.GcsDocument(gcs_uri=input_file, mime_type=mime_type),
                    ],
                ),
            ),
            document_output_config=documentai.DocumentOutputConfig(
                gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                    gcs_uri=f"gs://{temp_bucket}/ocr/{input_file.split('gs://')[-1]}",
                ),
            ),
        ),
    )
    operation.result()

    # Read the results of the Document AI operation from Cloud Storage.
    storage_client = storage.Client()
    metadata = documentai.BatchProcessMetadata(operation.metadata)
    output_gcs_path = metadata.individual_process_statuses[0].output_gcs_destination
    (output_bucket, output_prefix) = output_gcs_path.removeprefix("gs://").split("/", 1)
    for blob in storage_client.list_blobs(output_bucket, prefix=output_prefix):
        blob_contents = blob.download_as_bytes()
        document = documentai.Document.from_json(blob_contents, ignore_unknown_fields=True)
        yield document.text


def write_to_bigquery(
    event_id: str,
    time_uploaded: datetime,
    doc_path: str,
    auth_url: str,
    doc_text: str,
    doc_abstract: str,
    uploader: str,
    file_size: int,
    text_length: int,
    parent_folders: list[str],
    file_name: str,
    project: str,
    bq_dataset: str,
    bq_table: str,
) -> None:
    """Write the summary to BigQuery.

    Args:
        event_id: The Eventarc trigger event ID.
        time_uploaded: Time the document was uploaded.
        doc_path: Cloud Storage path to the document.
        auth_url: Authentication URL for accessing the document.
        doc_text: Text extracted from the document.
        doc_abstract: Summary generated fro the document.
        uploader: Identity of the user who uploaded the file.
        file_size: Size of the original file in bytes.
        text_length: Length of the extracted text in characters.
        parent_folders: List of parent folder names in the path.
        file_name: Name of the file without the path.
        project: Google Cloud project ID.
        bq_dataset: Name of the BigQuery dataset.
        bq_table: Name of the BigQuery table.
    """
    bq_client = bigquery.Client(project=project)
    location = os.environ["LOCATION"] 

    bq_client.insert_rows(
        table=bq_client.get_table(f"{bq_dataset}.{bq_table}"),
        rows=[
            {
                "event_id": event_id,
                "time_uploaded": time_uploaded,
                "time_processed": datetime.now(),
                "document_path": doc_path,
                "auth_url": auth_url,
                "document_text": doc_text,
                "document_abstract": doc_abstract,
                "uploader": uploader,
                "file_size_bytes": file_size,
                "text_length_chars": text_length,
                "parent_folders": parent_folders,
                "file_name": file_name,
            },
        ],
    )
