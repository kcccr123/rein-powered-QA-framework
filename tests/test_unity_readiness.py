"""Capability checks remain offline and cannot mistake a legacy bridge for QA."""

from dataclasses import replace

import pytest

from qa_framework.environments.unity import (
    UnityQACapabilities, UnityQAReadinessError, UnityQARequirements,
)


def complete_capabilities():
    return UnityQACapabilities(
        observation_size=119, action_size=5, role="qa_gameplay",
        observation_schema_version=1, ray_count=16,
        action_encoding_version=1, max_ability_slot=0,
        supports_level_recipe=True, acknowledges_level_identity=True,
        reports_terminal_reason=True, reports_episode_metrics=True,
        separates_termination_flags=True,
    )


def test_matching_sizes_from_legacy_handshake_do_not_prove_readiness():
    requirements = UnityQARequirements()
    legacy = UnityQACapabilities(observation_size=119, action_size=5)
    issues = requirements.readiness_issues(legacy)
    assert any("level recipe" in issue for issue in issues)
    assert any("completion/failure" in issue for issue in issues)
    assert any("role" in issue for issue in issues)
    with pytest.raises(UnityQAReadinessError):
        requirements.require_ready(legacy)


def test_complete_explicit_capabilities_match_without_io():
    assert UnityQARequirements().readiness_issues(complete_capabilities()) == ()
    UnityQARequirements().require_ready(complete_capabilities())


@pytest.mark.parametrize("field,value", [
    ("role", "pcg"), ("ray_count", 8), ("observation_size", 63),
    ("action_size", 10), ("observation_schema_version", 2),
    ("max_ability_slot", 3), ("action_encoding_version", 2),
    ("reports_episode_metrics", False), ("supports_level_recipe", False),
    ("acknowledges_level_identity", False), ("reports_terminal_reason", False),
    ("separates_termination_flags", False),
])
def test_schema_role_and_capabilities_are_all_required(field, value):
    with pytest.raises(UnityQAReadinessError):
        UnityQARequirements().require_ready(replace(complete_capabilities(), **{field: value}))


def test_alternate_ray_and_ability_configuration_requires_matching_evidence():
    caps = replace(complete_capabilities(), ray_count=8, observation_size=63, max_ability_slot=2)
    UnityQARequirements(ray_count=8, max_ability_slot=2).require_ready(caps)
