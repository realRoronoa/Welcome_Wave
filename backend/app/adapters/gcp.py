"""Google Cloud adapters: Cloud Storage for files, Pub/Sub for events."""
from __future__ import annotations

import json

TOPICS = {  # event type -> topic suffix; each topic has its own worker deployment on GKE
    "repo.connected": "ingestion", "repo.pushed": "ingestion", "document.uploaded": "ingestion",
    "meeting.recording_ready": "meetings",
    "pr.opened": "reviews", "pr.updated": "reviews", "pr.merged": "reviews",
    "onboarding.started": "reviews",
    "feedback.created": "reviews", "program.completed": "reviews",  # the Keeper and the Planner (docs/AGENTS.md)
    "mail.invitation": "reviews",  # an email, not an agent's work: any worker's container sends it (app/services/mailing.py)
}


class GcsStorage:
    def __init__(self, bucket: str):
        from google.cloud import storage

        self.client = storage.Client()
        self.bucket = bucket

    def _blob(self, uri: str):
        assert uri.startswith("gs://"), uri
        bucket, name = uri[5:].split("/", 1)
        return self.client.bucket(bucket).blob(name)

    def get(self, uri):
        return self._blob(uri).download_as_bytes()

    def put(self, uri, data, content_type="application/octet-stream"):
        self._blob(uri).upload_from_string(data, content_type=content_type)
        return uri

    def put_file(self, uri, src, content_type="application/octet-stream"):
        """Upload a (large) file object in chunks: a call recording never sits in memory whole."""
        self._blob(uri).upload_from_file(src, content_type=content_type)
        return uri

    def delete(self, uri):
        from google.api_core.exceptions import NotFound

        try:
            self._blob(uri).delete()
        except NotFound:
            return False
        return True

    def signed_url(self, uri: str, minutes: int = 60) -> str:
        """Short-lived link so the browser can play a private recording. Works on Cloud Run via IAM signBlob."""
        from datetime import timedelta

        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        return self._blob(uri).generate_signed_url(version="v4", expiration=timedelta(minutes=minutes), method="GET",
                                                   service_account_email=creds.service_account_email, access_token=creds.token)


class PubSubBus:
    def __init__(self, project: str, prefix: str, timeout_s: float = 8.0):
        from google.cloud import pubsub_v1

        self.publisher = pubsub_v1.PublisherClient()
        self.project, self.prefix = project, prefix
        self.timeout_s = timeout_s  # short: callers keep the event in the outbox, and GitHub gives a webhook only 10 s

    def topic_for(self, event_type: str) -> str:
        return self.publisher.topic_path(self.project, f"{self.prefix}-{TOPICS[event_type]}")

    def publish(self, topic, event):
        future = self.publisher.publish(self.topic_for(event["type"]), json.dumps(event).encode(), type=event["type"])
        future.result(timeout=self.timeout_s)
