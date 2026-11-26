"""Validation: checks one resource against every rule of a policy."""
from engine.policy import Policy, Rule


class ValidationError(Exception):
    pass


def validate_resource(resource: dict, policy: Policy) -> list[str]:
    """Return a list of violations. An empty list means the resource is allowed."""
    violations = []
    for rule in policy.rules:
        if rule.kind != resource.get("kind"):
            continue
        violations += check_labels(resource, rule)
        violations += check_privileged(resource, rule)
    return violations


def check_labels(resource: dict, rule: Rule) -> list[str]:
    labels = resource.get("metadata", {}).get("labels", {})
    return [f"{rule.name}: missing label '{key}'" for key in rule.require_labels if key not in labels]


def check_privileged(resource: dict, rule: Rule) -> list[str]:
    if not rule.deny_privileged:
        return []
    containers = resource.get("spec", {}).get("containers", [])
    return [
        f"{rule.name}: container '{c.get('name')}' must not be privileged"
        for c in containers
        if c.get("securityContext", {}).get("privileged")
    ]
