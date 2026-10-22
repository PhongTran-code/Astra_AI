"""Privacy-boundary and artifact publication contracts."""

from dataclasses import FrozenInstanceError

import pytest

from coworker.teams import ActorKind, ActorRef, ContractError, ResourceRef
from coworker.teams.sharing import (
    ArtifactPublication,
    ArtifactVisibility,
    ContextBoundary,
    ContextScope,
    ContextShare,
    HistoryPolicy,
    requires_explicit_share,
)


NOW = "2026-08-29T12:00:00+00:00"
OWNER = ActorRef(id="human_owner", kind=ActorKind.HUMAN)
PUBLISHER = ActorRef(id="agent_builder", kind=ActorKind.AGENT)


def boundary(scope, scope_id):
    return ContextBoundary(scope=scope, scope_id=scope_id)


def publication(**overrides):
    values = {
        "artifact_id": "artifact_1",
        "revision_id": "artifact_revision_1",
        "workspace_id": "workspace_1",
        "source_run_id": "run_1",
        "source_task_id": "task_1",
        "publisher": PUBLISHER,
        "requester_id": "human_owner",
        "visibility": ArtifactVisibility.TASK,
        "scope_id": "task_1",
        "filename": "report.md",
        "media_type": "text/markdown",
        "size_bytes": 42,
        "sha256": "a" * 64,
        "idempotency_key": "run_1:report.md:a",
        "correlation_id": "trace_1",
        "source_ref": ResourceRef(kind="run_output", id="output_1"),
        "metadata": {"labels": ["reviewed"]},
        "created_at": NOW,
    }
    values.update(overrides)
    return ArtifactPublication(**values)


def test_all_required_context_and_history_scopes_are_named():
    assert {scope.value for scope in ContextScope} == {
        "agent_private",
        "direct_message",
        "group",
        "task",
        "workspace",
        "run_scratch",
    }
    assert HistoryPolicy.SELECTED_CONTEXT.value == "selected_context"


@pytest.mark.parametrize("source_scope", [ContextScope.AGENT_PRIVATE, ContextScope.DIRECT_MESSAGE])
def test_private_or_dm_context_requires_explicit_share_to_group(source_scope):
    assert requires_explicit_share(
        boundary(source_scope, "source_1"),
        boundary(ContextScope.GROUP, "conversation_group"),
    )


def test_same_boundary_needs_no_share_and_workspace_can_flow_downward():
    group = boundary(ContextScope.GROUP, "conversation_1")
    assert not requires_explicit_share(group, group)
    assert not requires_explicit_share(
        boundary(ContextScope.WORKSPACE, "workspace_1"),
        boundary(ContextScope.TASK, "task_1"),
    )


def test_context_share_is_auditable_and_round_trips():
    share = ContextShare(
        share_id="share_1",
        workspace_id="workspace_1",
        resource=ResourceRef(kind="message", id="message_dm_1"),
        source=boundary(ContextScope.DIRECT_MESSAGE, "conversation_dm"),
        target=boundary(ContextScope.GROUP, "conversation_group"),
        shared_by=OWNER,
        idempotency_key="message_dm_1:share:conversation_group",
        correlation_id="trace_1",
        reason="Share the approved requirement with the project group.",
        created_at=NOW,
    )
    assert requires_explicit_share(share.source, share.target)
    assert ContextShare.from_dict(share.to_dict()) == share


def test_context_share_rejects_noop_and_missing_reason():
    same = boundary(ContextScope.TASK, "task_1")
    common = {
        "share_id": "share_1",
        "workspace_id": "workspace_1",
        "resource": ResourceRef(kind="artifact", id="artifact_1"),
        "source": same,
        "target": same,
        "shared_by": OWNER,
        "idempotency_key": "artifact_1:share",
        "correlation_id": "trace_1",
        "reason": "Share",
        "created_at": NOW,
    }
    with pytest.raises(ContractError, match="must differ"):
        ContextShare(**common)
    with pytest.raises(ContractError, match="reason"):
        ContextShare(
            **{
                **common,
                "target": boundary(ContextScope.WORKSPACE, "workspace_1"),
                "reason": "",
            }
        )


def test_artifact_publication_has_provenance_acl_hash_and_round_trip():
    artifact = publication()
    assert artifact.boundary == boundary(ContextScope.TASK, "task_1")
    assert ArtifactPublication.from_dict(artifact.to_dict()) == artifact
    with pytest.raises(AttributeError):
        artifact.metadata["labels"].append("changed")
    with pytest.raises(FrozenInstanceError):
        artifact.filename = "other.md"


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"filename": "../secret"}, "safe basename"),
        ({"media_type": "markdown"}, "MIME type"),
        ({"size_bytes": -1}, "non-negative"),
        ({"sha256": "not-a-hash"}, "SHA-256"),
        ({"metadata": {"bad": object()}}, "JSON-compatible"),
    ],
)
def test_artifact_publication_rejects_unsafe_or_unverifiable_data(changes, match):
    with pytest.raises(ContractError, match=match):
        publication(**changes)


@pytest.mark.parametrize(
    "visibility,scope",
    [
        (ArtifactVisibility.AGENT_PRIVATE, ContextScope.AGENT_PRIVATE),
        (ArtifactVisibility.DIRECT_MESSAGE, ContextScope.DIRECT_MESSAGE),
        (ArtifactVisibility.GROUP, ContextScope.GROUP),
        (ArtifactVisibility.TASK, ContextScope.TASK),
        (ArtifactVisibility.WORKSPACE, ContextScope.WORKSPACE),
    ],
)
def test_artifact_visibility_maps_to_exactly_one_context_boundary(visibility, scope):
    artifact = publication(visibility=visibility, scope_id="scope_1")
    assert artifact.boundary.scope is scope
