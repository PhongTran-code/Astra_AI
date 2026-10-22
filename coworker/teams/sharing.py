"""Context-boundary and artifact-publication contracts for Astra Team."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from .contracts import (
    ActorRef,
    ContractError,
    ResourceRef,
    _as_enum,
    _freeze_json_object,
    _optional_id,
    _required_id,
    _required_timestamp,
    _thaw_json,
    utc_now,
)


class ContextScope(str, Enum):
    AGENT_PRIVATE = "agent_private"
    DIRECT_MESSAGE = "direct_message"
    GROUP = "group"
    TASK = "task"
    WORKSPACE = "workspace"
    RUN_SCRATCH = "run_scratch"


class HistoryPolicy(str, Enum):
    ALL_HISTORY = "all_history"
    FROM_JOIN = "from_join"
    SELECTED_CONTEXT = "selected_context"


class ArtifactVisibility(str, Enum):
    AGENT_PRIVATE = "agent_private"
    DIRECT_MESSAGE = "direct_message"
    GROUP = "group"
    TASK = "task"
    WORKSPACE = "workspace"


_VISIBILITY_SCOPE = {
    ArtifactVisibility.AGENT_PRIVATE: ContextScope.AGENT_PRIVATE,
    ArtifactVisibility.DIRECT_MESSAGE: ContextScope.DIRECT_MESSAGE,
    ArtifactVisibility.GROUP: ContextScope.GROUP,
    ArtifactVisibility.TASK: ContextScope.TASK,
    ArtifactVisibility.WORKSPACE: ContextScope.WORKSPACE,
}
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ContextBoundary:
    scope: ContextScope
    scope_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", _as_enum(ContextScope, self.scope, "context scope"))
        object.__setattr__(self, "scope_id", _required_id(self.scope_id, "context scope id"))

    def to_dict(self) -> dict[str, str]:
        return {"scope": self.scope.value, "scope_id": self.scope_id}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ContextBoundary":
        if not isinstance(raw, Mapping):
            raise ContractError("context boundary must be an object")
        return cls(scope=raw.get("scope"), scope_id=raw.get("scope_id", ""))


def requires_explicit_share(source: ContextBoundary, target: ContextBoundary) -> bool:
    """Return whether moving durable context across these boundaries needs consent.

    Workspace knowledge may be selected into a narrower authorized context. Every
    other cross-boundary durable publication is explicit. Most importantly, private
    agent and DM material can never become group/workspace memory implicitly.
    """

    if not isinstance(source, ContextBoundary) or not isinstance(target, ContextBoundary):
        raise ContractError("source and target must be ContextBoundary values")
    if source == target:
        return False
    if source.scope is ContextScope.WORKSPACE:
        return False
    return True


@dataclass(frozen=True)
class ContextShare:
    """Auditable proof of an explicit durable context boundary crossing."""

    share_id: str
    workspace_id: str
    resource: ResourceRef
    source: ContextBoundary
    target: ContextBoundary
    shared_by: ActorRef
    idempotency_key: str
    correlation_id: str
    reason: str
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("share_id", "workspace_id", "idempotency_key", "correlation_id"):
            object.__setattr__(
                self,
                name,
                _required_id(getattr(self, name), name.replace("_", " ")),
            )
        if not isinstance(self.resource, ResourceRef):
            raise ContractError("shared resource must be a ResourceRef")
        if not isinstance(self.source, ContextBoundary) or not isinstance(
            self.target, ContextBoundary
        ):
            raise ContractError("context share requires source and target boundaries")
        if self.source == self.target:
            raise ContractError("context share source and target must differ")
        if not isinstance(self.shared_by, ActorRef):
            raise ContractError("shared_by must be an ActorRef")
        reason = str(self.reason or "").strip()
        if not reason:
            raise ContractError("context share reason is required")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "created_at", _required_timestamp(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "share_id": self.share_id,
            "workspace_id": self.workspace_id,
            "resource": self.resource.to_dict(),
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "shared_by": self.shared_by.to_dict(),
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ContextShare":
        if not isinstance(raw, Mapping):
            raise ContractError("context share must be an object")
        try:
            resource = ResourceRef.from_dict(raw.get("resource") or {})
            source = ContextBoundary.from_dict(raw.get("source") or {})
            target = ContextBoundary.from_dict(raw.get("target") or {})
            shared_by = ActorRef.from_dict(raw.get("shared_by") or {})
        except (TypeError, AttributeError) as error:
            raise ContractError("context share contains malformed nested records") from error
        return cls(
            share_id=raw.get("share_id", ""),
            workspace_id=raw.get("workspace_id", ""),
            resource=resource,
            source=source,
            target=target,
            shared_by=shared_by,
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            reason=raw.get("reason", ""),
            created_at=raw.get("created_at") or "",
        )


@dataclass(frozen=True)
class ArtifactPublication:
    """One immutable artifact revision with provenance and an explicit ACL scope."""

    artifact_id: str
    revision_id: str
    workspace_id: str
    source_run_id: str
    publisher: ActorRef
    requester_id: str
    visibility: ArtifactVisibility
    scope_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    idempotency_key: str
    correlation_id: str
    source_task_id: Optional[str] = None
    source_ref: Optional[ResourceRef] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "revision_id",
            "workspace_id",
            "source_run_id",
            "requester_id",
            "scope_id",
            "idempotency_key",
            "correlation_id",
        ):
            object.__setattr__(
                self,
                name,
                _required_id(getattr(self, name), name.replace("_", " ")),
            )
        object.__setattr__(
            self,
            "source_task_id",
            _optional_id(self.source_task_id, "source task id"),
        )
        if not isinstance(self.publisher, ActorRef):
            raise ContractError("artifact publisher must be an ActorRef")
        visibility = _as_enum(ArtifactVisibility, self.visibility, "artifact visibility")
        object.__setattr__(self, "visibility", visibility)
        filename = str(self.filename or "").strip()
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ContractError("artifact filename must be a safe basename")
        object.__setattr__(self, "filename", filename)
        media_type = str(self.media_type or "").strip().lower()
        if not media_type or "/" not in media_type:
            raise ContractError("artifact media_type must be a MIME type")
        object.__setattr__(self, "media_type", media_type)
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ContractError("artifact size_bytes must be a non-negative integer")
        if self.size_bytes < 0:
            raise ContractError("artifact size_bytes must be a non-negative integer")
        digest = str(self.sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ContractError("artifact sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(self, "sha256", digest)
        if self.source_ref is not None and not isinstance(self.source_ref, ResourceRef):
            raise ContractError("artifact source_ref must be a ResourceRef or null")
        object.__setattr__(self, "metadata", _freeze_json_object(self.metadata, "artifact metadata"))
        object.__setattr__(self, "created_at", _required_timestamp(self.created_at, "created_at"))

    @property
    def boundary(self) -> ContextBoundary:
        return ContextBoundary(_VISIBILITY_SCOPE[self.visibility], self.scope_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "revision_id": self.revision_id,
            "workspace_id": self.workspace_id,
            "source_run_id": self.source_run_id,
            "source_task_id": self.source_task_id,
            "publisher": self.publisher.to_dict(),
            "requester_id": self.requester_id,
            "visibility": self.visibility.value,
            "scope_id": self.scope_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "source_ref": self.source_ref.to_dict() if self.source_ref else None,
            "metadata": _thaw_json(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactPublication":
        if not isinstance(raw, Mapping):
            raise ContractError("artifact publication must be an object")
        source_ref_raw = raw.get("source_ref")
        try:
            publisher = ActorRef.from_dict(raw.get("publisher") or {})
            source_ref = (
                ResourceRef.from_dict(source_ref_raw) if source_ref_raw is not None else None
            )
        except (TypeError, AttributeError) as error:
            raise ContractError("artifact publication contains malformed nested records") from error
        return cls(
            artifact_id=raw.get("artifact_id", ""),
            revision_id=raw.get("revision_id", ""),
            workspace_id=raw.get("workspace_id", ""),
            source_run_id=raw.get("source_run_id", ""),
            source_task_id=raw.get("source_task_id"),
            publisher=publisher,
            requester_id=raw.get("requester_id", ""),
            visibility=raw.get("visibility"),
            scope_id=raw.get("scope_id", ""),
            filename=raw.get("filename", ""),
            media_type=raw.get("media_type", ""),
            size_bytes=raw.get("size_bytes", -1),
            sha256=raw.get("sha256", ""),
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            source_ref=source_ref,
            metadata=raw.get("metadata") or {},
            created_at=raw.get("created_at") or "",
        )
