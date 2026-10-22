"""Ephemeral secret leases for Team sandbox execution.

The durable :class:`coworker.secrets.SecretStore` remains the source of truth for
credentials.  This module is deliberately only a short-lived hand-off layer:
it keeps resolved values in process memory, never persists them, and consumes a
lease before a command starts.  Callers must still redact the values from any
tool output before returning that output to the model.
"""

from __future__ import annotations

import re
import secrets as _secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class SecretBrokerError(RuntimeError):
    """A secret could not be safely leased or consumed."""


class SecretReferenceError(SecretBrokerError):
    """A persisted secret reference is malformed or unavailable."""


@dataclass(frozen=True)
class SandboxSecretRef:
    """A non-secret pointer from a revision policy to one environment variable."""

    profile: str
    field: str
    environment: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SandboxSecretRef":
        if not isinstance(value, Mapping):
            raise SecretReferenceError("sandbox secret reference must be an object")
        return cls(
            profile=_safe_reference(value.get("profile"), "secret profile"),
            field=_safe_reference(value.get("field"), "secret field"),
            environment=_safe_environment_name(value.get("environment", value.get("env"))),
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", _safe_reference(self.profile, "secret profile"))
        object.__setattr__(self, "field", _safe_reference(self.field, "secret field"))
        object.__setattr__(self, "environment", _safe_environment_name(self.environment))


@dataclass(frozen=True, repr=False)
class SecretLease:
    """Opaque metadata returned to the caller; it contains no secret material."""

    lease_id: str
    run_id: str
    expires_at: float = field(repr=False)

    def __repr__(self) -> str:
        return f"SecretLease(lease_id={self.lease_id!r}, run_id={self.run_id!r})"


@dataclass(frozen=True, repr=False)
class SecretInjection:
    """One-use environment material plus values used for output redaction.

    ``repr`` intentionally omits both mappings so accidental diagnostic output
    cannot expose a credential.
    """

    environment: Mapping[str, str] = field(repr=False)
    redaction_values: tuple[str, ...] = field(repr=False)

    def __repr__(self) -> str:
        return f"SecretInjection(environment_keys={tuple(self.environment)!r})"


@dataclass
class _LeaseState:
    run_id: str
    expires_at: float
    injection: SecretInjection = field(repr=False)


class EphemeralSecretBroker:
    """Resolve explicitly authorized references into one-use in-memory leases.

    ``resolver`` should be a callable equivalent to ``SecretStore.get``.  The
    optional profile authorizer is a second boundary: a Team revision may carry
    only references that the host has explicitly allowed for Team execution.
    """

    _MAX_TTL_SECONDS = 300

    def __init__(
        self,
        resolver: Callable[[str], Mapping[str, Any] | None],
        *,
        profile_authorizer: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(resolver):
            raise TypeError("secret resolver must be callable")
        self._resolver = resolver
        self._profile_authorizer = profile_authorizer
        self._clock = clock
        self._lock = threading.RLock()
        self._leases: dict[str, _LeaseState] = {}
        self._run_redactions: dict[str, tuple[str, ...]] = {}

    def issue(
        self,
        run_id: str,
        references: Sequence[SandboxSecretRef | Mapping[str, Any]],
        *,
        ttl_seconds: int = 60,
    ) -> SecretLease:
        run_id = _safe_reference(run_id, "run id")
        if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
            raise SecretReferenceError("sandbox secret references must be a sequence")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise SecretReferenceError("secret lease TTL must be a positive integer")
        if ttl_seconds <= 0 or ttl_seconds > self._MAX_TTL_SECONDS:
            raise SecretReferenceError(
                f"secret lease TTL must be between 1 and {self._MAX_TTL_SECONDS} seconds"
            )

        refs = tuple(
            item if isinstance(item, SandboxSecretRef) else SandboxSecretRef.from_mapping(item)
            for item in references
        )
        if not refs:
            raise SecretReferenceError("at least one secret reference is required")
        environments = [item.environment for item in refs]
        if len(set(environments)) != len(environments):
            raise SecretReferenceError("sandbox secret environment names must be unique")

        environment: dict[str, str] = {}
        for reference in refs:
            if self._profile_authorizer is not None:
                try:
                    allowed = bool(self._profile_authorizer(reference.profile))
                except Exception as error:
                    raise SecretReferenceError("secret profile authorization failed") from error
                if not allowed:
                    raise SecretReferenceError("secret profile is not authorized for Team sandbox")
            try:
                profile = self._resolver(reference.profile)
            except Exception as error:
                raise SecretReferenceError("secret profile is unavailable") from error
            if not isinstance(profile, Mapping):
                raise SecretReferenceError("secret profile is unavailable")
            value = profile.get(reference.field)
            if not isinstance(value, str) or not value:
                raise SecretReferenceError("secret field is unavailable")
            # SecretStore leaves unresolved ${ENV_VAR} references intact.  Never
            # pass that placeholder to a sandbox as if it were a real secret.
            if re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", value):
                raise SecretReferenceError("secret field is unresolved")
            environment[reference.environment] = value

        now = self._clock()
        lease_id = "secret_" + _secrets.token_urlsafe(24)
        lease = SecretLease(lease_id, run_id, now + ttl_seconds)
        with self._lock:
            self._purge_locked(now)
            self._leases[lease_id] = _LeaseState(
                run_id=run_id,
                expires_at=lease.expires_at,
                injection=SecretInjection(
                    environment=dict(environment),
                    redaction_values=tuple(sorted(set(environment.values()), key=len, reverse=True)),
                ),
            )
            self._run_redactions[run_id] = tuple(
                sorted(
                    set(self._run_redactions.get(run_id, ())) | set(environment.values()),
                    key=len,
                    reverse=True,
                )
            )
        return lease

    def consume(self, lease: SecretLease, *, run_id: str) -> SecretInjection:
        if not isinstance(lease, SecretLease):
            raise SecretBrokerError("secret lease is invalid")
        run_id = _safe_reference(run_id, "run id")
        with self._lock:
            state = self._leases.get(lease.lease_id)
            if state is None:
                raise SecretBrokerError("secret lease is expired or already consumed")
            if state.run_id != run_id or lease.run_id != run_id:
                raise SecretBrokerError("secret lease belongs to another run")
            if self._clock() >= state.expires_at:
                del self._leases[lease.lease_id]
                if not any(item.run_id == state.run_id for item in self._leases.values()):
                    self._run_redactions.pop(state.run_id, None)
                raise SecretBrokerError("secret lease is expired")
            # Delete before returning: a retry or re-entrant call cannot reuse it.
            del self._leases[lease.lease_id]
            return state.injection

    def revoke_run(self, run_id: str) -> int:
        run_id = _safe_reference(run_id, "run id")
        with self._lock:
            doomed = [lease_id for lease_id, state in self._leases.items() if state.run_id == run_id]
            for lease_id in doomed:
                del self._leases[lease_id]
            self._run_redactions.pop(run_id, None)
            return len(doomed)

    def redact_for_run(self, run_id: str, value: str) -> str:
        """Return a redacted value using only the current run's memory lease."""

        run_id = _safe_reference(run_id, "run id")
        with self._lock:
            values = self._run_redactions.get(run_id, ())
        return redact_secret_output(value, values)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_locked(self._clock())

    def active_lease_count(self) -> int:
        with self._lock:
            self._purge_locked(self._clock())
            return len(self._leases)

    def _purge_locked(self, now: float) -> int:
        doomed = [
            lease_id for lease_id, state in self._leases.items() if now >= state.expires_at
        ]
        doomed_runs = {self._leases[lease_id].run_id for lease_id in doomed}
        for lease_id in doomed:
            del self._leases[lease_id]
        active_runs = {state.run_id for state in self._leases.values()}
        for run_id in doomed_runs - active_runs:
            self._run_redactions.pop(run_id, None)
        return len(doomed)


def redact_secret_output(value: str, redaction_values: Sequence[str]) -> str:
    """Replace known secret values without ever logging or returning them."""

    result = str(value)
    for secret in sorted(
        (item for item in redaction_values if isinstance(item, str) and len(item) >= 4),
        key=len,
        reverse=True,
    ):
        result = result.replace(secret, "[REDACTED]")
    return result


def _safe_reference(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 256 or "\x00" in value:
        raise SecretReferenceError(f"{label} is invalid")
    return value


def _safe_environment_name(value: Any) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SecretReferenceError("secret environment name is invalid")
    return value
