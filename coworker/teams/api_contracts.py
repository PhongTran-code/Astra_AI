"""Transport-neutral command, query, and error envelopes for Team APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from .contracts import (
    ActorRef,
    ContractError,
    _freeze_json_object,
    _optional_id,
    _required_id,
    _required_kind,
    _required_timestamp,
    _thaw_json,
    utc_now,
)


class ApiErrorCode(str, Enum):
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    STALE_REVISION = "stale_revision"
    APPROVAL_REQUIRED = "approval_required"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEPENDENCY_BLOCKED = "dependency_blocked"
    OFFLINE = "offline"
    CANCELED = "canceled"
    RATE_LIMITED = "rate_limited"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True)
class CommandEnvelope:
    """One mutating REST intent before domain authorization and execution."""

    command_id: str
    command_type: str
    workspace_id: str
    requester: ActorRef
    idempotency_key: str
    correlation_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    expected_version: Optional[int] = None
    causation_id: Optional[str] = None
    requested_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("command_id", "workspace_id", "idempotency_key", "correlation_id"):
            object.__setattr__(
                self,
                name,
                _required_id(getattr(self, name), name.replace("_", " ")),
            )
        object.__setattr__(
            self,
            "command_type",
            _required_kind(self.command_type, "command type"),
        )
        if not isinstance(self.requester, ActorRef):
            raise ContractError("command requester must be an ActorRef")
        if self.expected_version is not None and (
            isinstance(self.expected_version, bool)
            or not isinstance(self.expected_version, int)
            or self.expected_version < 0
        ):
            raise ContractError("expected_version must be a non-negative integer or null")
        object.__setattr__(
            self,
            "causation_id",
            _optional_id(self.causation_id, "causation id"),
        )
        object.__setattr__(self, "payload", _freeze_json_object(self.payload, "command payload"))
        object.__setattr__(
            self,
            "requested_at",
            _required_timestamp(self.requested_at, "requested_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type,
            "workspace_id": self.workspace_id,
            "requester": self.requester.to_dict(),
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "payload": _thaw_json(self.payload),
            "expected_version": self.expected_version,
            "causation_id": self.causation_id,
            "requested_at": self.requested_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CommandEnvelope":
        if not isinstance(raw, Mapping):
            raise ContractError("command envelope must be an object")
        try:
            requester = ActorRef.from_dict(raw.get("requester") or {})
        except (TypeError, AttributeError) as error:
            raise ContractError("command contains a malformed requester") from error
        return cls(
            command_id=raw.get("command_id", ""),
            command_type=raw.get("command_type", ""),
            workspace_id=raw.get("workspace_id", ""),
            requester=requester,
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            payload=raw.get("payload") or {},
            expected_version=raw.get("expected_version"),
            causation_id=raw.get("causation_id"),
            requested_at=raw.get("requested_at") or utc_now(),
        )


@dataclass(frozen=True)
class QueryEnvelope:
    """One authorized REST query with bounded cursor pagination."""

    query_id: str
    query_type: str
    workspace_id: str
    requester: ActorRef
    correlation_id: str
    filters: Mapping[str, Any] = field(default_factory=dict)
    cursor: Optional[str] = None
    limit: int = 50
    requested_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("query_id", "workspace_id", "correlation_id"):
            object.__setattr__(
                self,
                name,
                _required_id(getattr(self, name), name.replace("_", " ")),
            )
        object.__setattr__(self, "query_type", _required_kind(self.query_type, "query type"))
        if not isinstance(self.requester, ActorRef):
            raise ContractError("query requester must be an ActorRef")
        cursor = None if self.cursor is None else str(self.cursor).strip()
        if cursor == "" or (cursor is not None and len(cursor) > 2048):
            raise ContractError("query cursor must be null or a non-empty opaque value")
        object.__setattr__(self, "cursor", cursor)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise ContractError("query limit must be an integer between 1 and 200")
        if not 1 <= self.limit <= 200:
            raise ContractError("query limit must be an integer between 1 and 200")
        object.__setattr__(self, "filters", _freeze_json_object(self.filters, "query filters"))
        object.__setattr__(
            self,
            "requested_at",
            _required_timestamp(self.requested_at, "requested_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_type": self.query_type,
            "workspace_id": self.workspace_id,
            "requester": self.requester.to_dict(),
            "correlation_id": self.correlation_id,
            "filters": _thaw_json(self.filters),
            "cursor": self.cursor,
            "limit": self.limit,
            "requested_at": self.requested_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QueryEnvelope":
        if not isinstance(raw, Mapping):
            raise ContractError("query envelope must be an object")
        try:
            requester = ActorRef.from_dict(raw.get("requester") or {})
        except (TypeError, AttributeError) as error:
            raise ContractError("query contains a malformed requester") from error
        return cls(
            query_id=raw.get("query_id", ""),
            query_type=raw.get("query_type", ""),
            workspace_id=raw.get("workspace_id", ""),
            requester=requester,
            correlation_id=raw.get("correlation_id", ""),
            filters=raw.get("filters") or {},
            cursor=raw.get("cursor"),
            limit=raw.get("limit", 50),
            requested_at=raw.get("requested_at") or utc_now(),
        )


@dataclass(frozen=True)
class ApiErrorEnvelope:
    code: ApiErrorCode
    message: str
    correlation_id: str
    retryable: bool = False
    retry_after_seconds: Optional[int] = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            code = self.code if isinstance(self.code, ApiErrorCode) else ApiErrorCode(self.code)
        except (TypeError, ValueError) as error:
            raise ContractError(f"invalid API error code: {self.code!r}") from error
        object.__setattr__(self, "code", code)
        message = str(self.message or "").strip()
        if not message:
            raise ContractError("API error message is required")
        object.__setattr__(self, "message", message)
        object.__setattr__(
            self,
            "correlation_id",
            _required_id(self.correlation_id, "correlation id"),
        )
        if not isinstance(self.retryable, bool):
            raise ContractError("retryable must be a boolean")
        if self.retry_after_seconds is not None and (
            isinstance(self.retry_after_seconds, bool)
            or not isinstance(self.retry_after_seconds, int)
            or self.retry_after_seconds < 0
        ):
            raise ContractError("retry_after_seconds must be non-negative or null")
        if self.retry_after_seconds is not None and not self.retryable:
            raise ContractError("retry_after_seconds requires retryable=true")
        object.__setattr__(self, "details", _freeze_json_object(self.details, "error details"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "details": _thaw_json(self.details),
        }
