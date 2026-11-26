"""Google Cloud adapters: Cloud Storage for files, Pub/Sub for events."""
from __future__ import annotations

import json
from contextlib import contextmanager

TOPICS = {  # event type -> topic suffix; each topic has its own worker deployment on GKE
    "repo.connected": "ingestion", "repo.pushed": "ingestion", "document.uploaded": "ingestion",
    "meeting.recording_ready": "meetings",
    "pr.opened": "reviews", "pr.updated": "reviews", "pr.merged": "reviews",
    "onboarding.started": "reviews",
    "feedback.created": "reviews", "program.completed": "reviews",  # the Keeper and the Planner (docs/AGENTS.md)
    # An email, not an agent's work: the review worker sends it with its own SMTP settings, which the deployment gives it
    # (infra/k8s/workers.yaml: SMTP_* and PUBLIC_URL in worker-config, SMTP_PASSWORD from Secret Manager).
    "mail.invitation": "reviews",
}

# Resumable uploads send the file in chunks of this size (a multiple of 256 KiB): what an import holds in memory at once.
WRITE_CHUNK = 8 << 20


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

    @contextmanager
    def open_writer(self, uri: str, content_type: str):
        """A file to write a large object into as it arrives (a Drive import): a resumable upload sent WRITE_CHUNK at a time,
        so neither memory nor the instance's in-memory /tmp holds the file. The object exists only once the block ends
        without an error; an error cancels the upload. It never replaces an object that is already there."""
        blob = self._blob(uri)
        with blob.open("wb", chunk_size=WRITE_CHUNK, content_type=content_type, if_generation_match=0) as w:
            yield w

    def stat(self, uri: str) -> dict | None:
        """{"size", "content_type"} of a stored object, or None when there is none."""
        bucket, name = uri[5:].split("/", 1)
        blob = self.client.bucket(bucket).get_blob(name)
        if blob is None:
            return None
        return {"size": int(blob.size or 0), "content_type": blob.content_type or ""}

    def head(self, uri: str, n: int) -> bytes:
        """The first `n` bytes of an object (to check what kind of file it really is)."""
        return self._blob(uri).download_as_bytes(start=0, end=max(0, n - 1))

    def delete(self, uri):
        from google.api_core.exceptions import NotFound

        try:
            self._blob(uri).delete()
        except NotFound:
            return False
        return True

    def _signing(self) -> dict:
        """How URLs are signed. On Cloud Run there is no key file: the service account signs through IAM signBlob
        (roles/iam.serviceAccountTokenCreator on itself, infra/terraform/iam.tf)."""
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        return {"service_account_email": creds.service_account_email, "access_token": creds.token}

    def signed_url(self, uri: str, minutes: int = 60) -> str:
        """Short-lived link so the browser can play a private recording. Works on Cloud Run via IAM signBlob."""
        from datetime import timedelta

        return self._blob(uri).generate_signed_url(version="v4", expiration=timedelta(minutes=minutes), method="GET", **self._signing())

    def signed_upload(self, uri: str, content_type: str, size: int, minutes: int = 15) -> tuple[str, dict[str, str]]:
        """A V4 signed URL the browser PUTs one file to, straight into Cloud Storage (Cloud Run never carries it). The
        signature binds the content type, the exact size (x-goog-content-length-range) and "create only"
        (x-goog-if-generation-match: 0), so the URL cannot store anything else, or replace the file once it is there.
        Returns the URL and the headers the browser must send with exactly these values. The PUT must start within
        `minutes`; a long upload that started in time finishes. The bucket's CORS rule (infra/terraform/data.tf) lets the
        web app's origin send these headers."""
        from datetime import timedelta

        headers = {"x-goog-content-length-range": f"{int(size)},{int(size)}", "x-goog-if-generation-match": "0"}
        url = self._blob(uri).generate_signed_url(version="v4", expiration=timedelta(minutes=minutes), method="PUT",
                                                  content_type=content_type, headers=dict(headers),  # a copy: the library adds "host"
                                                  **self._signing())
        return url, {"Content-Type": content_type, **headers}


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
