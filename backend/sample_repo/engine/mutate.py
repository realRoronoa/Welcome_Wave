"""Mutation: adds default labels before validation runs."""

DEFAULT_LABELS = {"managed-by": "mini-policy-engine"}


def apply_defaults(resource: dict) -> dict:
    meta = resource.setdefault("metadata", {})
    labels = meta.setdefault("labels", {})
    for key, value in DEFAULT_LABELS.items():
        labels.setdefault(key, value)
    return resource
