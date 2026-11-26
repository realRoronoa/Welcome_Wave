"""Policy model and loader."""
from dataclasses import dataclass, field

import yaml


@dataclass
class Rule:
    name: str
    kind: str
    require_labels: list[str] = field(default_factory=list)
    deny_privileged: bool = False


@dataclass
class Policy:
    name: str
    rules: list[Rule]
    enforce: bool = True


def load_policy(text: str) -> Policy:
    """Parse a policy from YAML. Unknown keys are ignored on purpose."""
    data = yaml.safe_load(text) or {}
    rules = [Rule(**r) for r in data.get("rules", [])]
    return Policy(name=data["name"], rules=rules, enforce=data.get("enforce", True))
