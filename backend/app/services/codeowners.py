"""Who should answer a question about a path: CODEOWNERS first, frequent committers second."""
from __future__ import annotations

import fnmatch


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    """(pattern, owners without '@') per rule line, in file order. A pattern with no owners is kept with owners=[]:
    GitHub reads it as "these paths have no code owner", and as the last matching rule it overrides a broader one
    ('/engine/ @ravi-dev' then '/engine/policy.py' leaves policy.py unowned)."""
    rules = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        rules.append((pattern, [o.lstrip("@") for o in owners]))
    return rules


def has_owners(rules: list[tuple[str, list[str]]]) -> bool:
    """At least one rule names an owner: a file of owner-less patterns alone assigns nobody."""
    return any(owners for _, owners in rules)


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.startswith("/")
    p = pattern.lstrip("/")
    if p.endswith("/"):
        return path.startswith(p) if anchored else (path.startswith(p) or f"/{p}" in f"/{path}")
    if "*" in p or "?" in p:
        return fnmatch.fnmatch(path, p) or (not anchored and fnmatch.fnmatch(path.split("/")[-1], p))
    return path == p or path.startswith(p + "/") or (not anchored and path.endswith("/" + p))


def owners_for(rules: list[tuple[str, list[str]]], path: str) -> list[str]:
    """GitHub semantics: the last matching rule wins, and a matching rule with no owners leaves the path unowned ([])."""
    found = rule_for(rules, path)
    return found[1] if found else []


def rule_for(rules: list[tuple[str, list[str]]], path: str) -> tuple[str, list[str]] | None:
    """The rule that decides `path`'s owners (the last match), e.g. ('/engine/', ['ravi-dev']), or ('/engine/policy.py', [])
    for an owner-less pattern. None when no rule matches."""
    found = None
    for pattern, owners in rules:
        if pattern == "*" or _matches(pattern, path):
            found = (pattern, owners)
    return found
