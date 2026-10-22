"""Command/query/realtime/error transport contract tests."""

import pytest

from coworker.teams import ActorKind, ActorRef, ContractError, DomainEvent, new_domain_id
from coworker.teams.api_contracts import (
    ApiErrorCode,
    ApiErrorEnvelope,
    CommandEnvelope,
    QueryEnvelope,
)
from coworker.teams.contracts import CANONICAL_ID_PREFIXES, DomainIdPrefix


NOW = "2026-08-29T12:00:00+00:00"
OWNER = ActorRef(id="human_owner", kind=ActorKind.HUMAN)


def test_every_canonical_id_prefix_generates_an_opaque_id():
    assert CANONICAL_ID_PREFIXES == {prefix.value for prefix in DomainIdPrefix}
    for prefix in DomainIdPrefix:
        value = new_domain_id(prefix)
        assert value.startswith(f"{prefix.value}_")
        assert len(value) == len(prefix.value) + 33
    with pytest.raises(ContractError, match="unknown canonical"):
        new_domain_id("team")


def test_command_envelope_round_trips_and_freezes_nested_payload():
    command = CommandEnvelope(
        command_id="command_1",
        command_type="conversation.message.create",
        workspace_id="workspace_1",
        requester=OWNER,
        idempotency_key="client_message_1",
        correlation_id="trace_1",
        payload={"mentions": ["agent_builder"]},
        expected_version=3,
        requested_at=NOW,
    )
    assert CommandEnvelope.from_dict(command.to_dict()) == command
    with pytest.raises(AttributeError):
        command.payload["mentions"].append("agent_reviewer")


@pytest.mark.parametrize("version", [-1, True, 1.5])
def test_command_expected_version_is_non_negative_or_null(version):
    with pytest.raises(ContractError, match="expected_version"):
        CommandEnvelope(
            command_id="command_1",
            command_type="task.update",
            workspace_id="workspace_1",
            requester=OWNER,
            idempotency_key="update_1",
            correlation_id="trace_1",
            expected_version=version,
            requested_at=NOW,
        )


def test_query_envelope_supports_opaque_cursor_and_bounded_limit():
    query = QueryEnvelope(
        query_id="query_1",
        query_type="conversation.messages.list",
        workspace_id="workspace_1",
        requester=OWNER,
        correlation_id="trace_1",
        filters={"conversation_id": "conversation_1"},
        cursor="opaque/base64==",
        limit=100,
        requested_at=NOW,
    )
    assert QueryEnvelope.from_dict(query.to_dict()) == query
    with pytest.raises(ContractError, match="between 1 and 200"):
        QueryEnvelope(
            query_id="query_2",
            query_type="messages.list",
            workspace_id="workspace_1",
            requester=OWNER,
            correlation_id="trace_1",
            limit=500,
            requested_at=NOW,
        )


def test_domain_event_version_and_sequence_form_realtime_cursor_contract():
    event = DomainEvent(
        event_id="event_1",
        event_type="message.created",
        workspace_id="workspace_1",
        actor_id="human_owner",
        aggregate_type="conversation",
        aggregate_id="conversation_1",
        correlation_id="trace_1",
        sequence=7,
        schema_version=2,
        occurred_at=NOW,
    )
    restored = DomainEvent.from_dict(event.to_dict())
    assert (restored.sequence, restored.schema_version) == (7, 2)
    with pytest.raises(ContractError, match="schema_version"):
        DomainEvent.from_dict({**event.to_dict(), "schema_version": 0})


def test_api_error_taxonomy_carries_retry_contract_and_safe_details():
    error = ApiErrorEnvelope(
        code=ApiErrorCode.TEMPORARILY_UNAVAILABLE,
        message="Sandbox provider is offline.",
        correlation_id="trace_1",
        retryable=True,
        retry_after_seconds=15,
        details={"sandbox_id": "sandbox_1"},
    )
    assert error.to_dict()["code"] == "temporarily_unavailable"
    with pytest.raises(ContractError, match="requires retryable"):
        ApiErrorEnvelope(
            code="offline",
            message="Offline",
            correlation_id="trace_1",
            retry_after_seconds=5,
        )
