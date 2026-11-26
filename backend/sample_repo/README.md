# Mini Policy Engine

A tiny admission-control policy engine used as the demo project for Welcome Wave.
Policies are loaded from YAML, resources are validated against them, and the webhook
server answers Kubernetes admission requests.

## Run
    python -m webhooks.server
