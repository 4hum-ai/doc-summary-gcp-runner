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
import base64
from datetime import datetime

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import tasks_v2

# Initialize Cloud Tasks client globally to reuse connection
tasks_client = tasks_v2.CloudTasksClient()


@functions_framework.cloud_event
def on_cloud_event(event: CloudEvent) -> None:
    """Receives an Eventarc event, extracts details, and enqueues a Cloud Task
    for background processing, acknowledging the original event immediately.

    Args:
        event: CloudEvent object.
    """
    event_id = "unknown"
    try:
        # --- Configuration --- Requires Environment Variables
        project_id = os.environ["PROJECT_ID"]
        location = os.environ["LOCATION"] # Region for Cloud Tasks queue
        queue_id = os.environ["TASK_QUEUE_ID"] # Name of your Cloud Tasks queue
        processor_url = os.environ["PROCESSOR_FUNCTION_URL"] # URL of the HTTP processor function
        # --- End Configuration ---

        event_data = event.data
        event_id = event_data.get("id", "N/A")
        event_type = event._attributes["type"]

        print(f"{event_id}: Received event type '{event_type}'.")

        # --- Prepare Task Payload --- #
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "bucket": event_data.get("bucket"),
            "filename": event_data.get("name"),
            "content_type": event_data.get("contentType"), # Present for 'finalized'
            "time_created": event_data.get("timeCreated"), # Present for 'finalized'
            # Add any other necessary data from the original event
        }
        print(f"payload: {payload}")
        # Basic validation
        if not payload["bucket"] or not payload["filename"]:
            logging.error(f"{event_id}: Missing 'bucket' or 'filename' in event data.")
            return

        # --- Create Cloud Task --- #
        # Encode payload as base64 to handle non-ASCII characters
        json_payload = json.dumps(payload)
        # Ensure proper base64 padding
        encoded_payload = base64.b64encode(json_payload.encode('utf-8')).decode('utf-8')
        
        task = {
            "http_request": {
                "http_method": "POST",
                "url": processor_url,
                "headers": {
                    "Content-Type": "application/json",
                    "Content-Transfer-Encoding": "base64"
                },
                "body": encoded_payload
            }
        }

        # --- Enqueue Cloud Task --- #
        parent = tasks_client.queue_path(project_id, location, queue_id)
        response = tasks_client.create_task(request={"parent": parent, "task": task})

        print(f"{event_id}: Cloud Task created with name: {response.name}")

    except Exception as e:
        logging.error(f"{event_id}: Error processing event: {e}")
        raise  # Re-raise the exception to ensure proper error handling

    finally:
        print(f"{event_id}: Event processing completed.") 