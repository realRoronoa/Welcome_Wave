from engine.policy import Policy, Rule
from engine.validate import validate_resource


def test_missing_label_is_reported():
    policy = Policy(name="p", rules=[Rule(name="team-label", kind="Pod", require_labels=["team"])])
    assert validate_resource({"kind": "Pod", "metadata": {"labels": {}}}, policy) == ["team-label: missing label 'team'"]
