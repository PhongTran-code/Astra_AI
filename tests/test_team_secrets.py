"""Ephemeral Team sandbox secret lease tests."""

from __future__ import annotations

import pytest

from coworker.teams import (
    EphemeralSecretBroker,
    SandboxSecretRef,
    SecretBrokerError,
    SecretReferenceError,
    redact_secret_output,
)


def test_secret_lease_is_one_use_and_never_repr_leaks_material():
    broker = EphemeralSecretBroker(
        lambda profile: {"token": "team-secret-value"} if profile == "team:ci" else None,
        profile_authorizer=lambda profile: profile.startswith("team:"),
    )
    lease = broker.issue(
        "run_1",
        ({"profile": "team:ci", "field": "token", "environment": "CI_TOKEN"},),
    )

    assert "team-secret-value" not in repr(lease)
    injection = broker.consume(lease, run_id="run_1")
    assert injection.environment == {"CI_TOKEN": "team-secret-value"}
    assert "team-secret-value" not in repr(injection)
    assert broker.active_lease_count() == 0
    with pytest.raises(SecretBrokerError, match="expired or already consumed"):
        broker.consume(lease, run_id="run_1")


def test_secret_lease_expires_and_profile_scope_is_fail_closed():
    now = [100.0]
    broker = EphemeralSecretBroker(
        lambda _profile: {"token": "value"},
        profile_authorizer=lambda profile: profile == "team:allowed",
        clock=lambda: now[0],
    )
    lease = broker.issue(
        "run_2",
        (SandboxSecretRef("team:allowed", "token", "TOKEN"),),
        ttl_seconds=2,
    )
    now[0] = 102.0
    with pytest.raises(SecretBrokerError, match="expired"):
        broker.consume(lease, run_id="run_2")
    with pytest.raises(SecretReferenceError, match="not authorized"):
        broker.issue(
            "run_3",
            ({"profile": "github:default", "field": "token", "env": "TOKEN"},),
        )


def test_secret_output_redaction_prefers_longest_values_first():
    assert redact_secret_output(
        "prefix team-secret-value and team-secret-value-suffix", 
        ("team-secret-value", "team-secret"),
    ) == "prefix [REDACTED] and [REDACTED]-suffix"
