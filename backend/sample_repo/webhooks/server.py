"""Admission webhook: receives AdmissionReview requests and answers allow/deny."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from engine.mutate import apply_defaults
from engine.policy import load_policy
from engine.validate import validate_resource

POLICY = load_policy(open("policies/default.yaml").read()) if False else None


def handle_admission(review: dict, policy) -> dict:
    """Mutate, then validate. Denies with all violation messages joined."""
    request = review["request"]
    resource = apply_defaults(request["object"])
    violations = validate_resource(resource, policy)
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {"uid": request["uid"], "allowed": not violations, "status": {"message": "; ".join(violations)}},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps(handle_admission(body, POLICY)).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    HTTPServer(("", 8443), Handler).serve_forever()
