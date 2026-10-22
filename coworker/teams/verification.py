"""Acceptance contracts and independent, durable verification attempts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import ActorKind, ContractError, new_domain_id
from .persistence import PersistenceConflictError, TeamDatabase, WorkRepository
from .permissions import redact_sensitive


class VerificationError(ContractError):
    """A verification request is malformed or violates reviewer authority."""


@dataclass(frozen=True)
class AcceptanceContract:
    contract_id: str
    kind: str
    checks: tuple[Mapping[str, Any], ...]
    human_review_required: bool = False

    def __post_init__(self) -> None:
        contract_id = str(self.contract_id or "").strip()
        kind = str(self.kind or "").strip().lower()
        if not contract_id or not kind:
            raise VerificationError("acceptance contract needs an id and kind")
        if kind not in {"coding", "research", "browser", "connector", "crew", "custom"}:
            raise VerificationError("unsupported acceptance contract kind")
        if not self.checks or len(self.checks) > 32:
            raise VerificationError("acceptance contract needs between 1 and 32 checks")
        normalized: list[Mapping[str, Any]] = []
        for check in self.checks:
            if not isinstance(check, Mapping):
                raise VerificationError("acceptance check must be an object")
            check_kind = str(check.get("kind") or "").strip().lower()
            check_id = str(check.get("check_id") or "").strip()
            if not check_kind or not check_id or len(check_id) > 160:
                raise VerificationError("acceptance check needs kind and check_id")
            normalized.append(_safe_json_value(check))
        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "checks", tuple(normalized))
        if not isinstance(self.human_review_required, bool):
            raise VerificationError("human_review_required must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "kind": self.kind,
            "checks": [dict(check) for check in self.checks],
            "human_review_required": self.human_review_required,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AcceptanceContract":
        if not isinstance(raw, Mapping):
            raise VerificationError("acceptance contract must be an object")
        checks = raw.get("checks")
        if isinstance(checks, (str, bytes)) or not isinstance(checks, Sequence):
            raise VerificationError("acceptance checks must be a sequence")
        return cls(
            contract_id=raw.get("contract_id", ""),
            kind=raw.get("kind", ""),
            checks=tuple(checks),
            human_review_required=raw.get("human_review_required", False),
        )


@dataclass(frozen=True)
class VerificationAttempt:
    verification_attempt_id: str
    run_id: str
    task_id: str | None
    verifier_actor_id: str
    status: str
    evidence: tuple[Mapping[str, Any], ...] = ()
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_attempt_id": self.verification_attempt_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "verifier_actor_id": self.verifier_actor_id,
            "status": self.status,
            "evidence": [dict(item) for item in self.evidence],
            "failure_reason": self.failure_reason,
        }


class VerificationRepository:
    """Durable storage for every verification decision and its safe evidence."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_attempt(
        self,
        attempt_id: str,
        workspace_id: str,
        run_id: str,
        verifier_actor_id: str,
        contract: AcceptanceContract,
        *,
        task_id: str | None,
        status: str,
        evidence: Sequence[Mapping[str, Any]] = (),
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        attempt_id = _id(attempt_id, "verification attempt id")
        workspace_id = _id(workspace_id, "verification workspace id")
        run_id = _id(run_id, "verification run id")
        verifier_actor_id = _id(verifier_actor_id, "verification actor id")
        task_id = _optional_id(task_id)
        if status not in {"passed", "failed"}:
            raise VerificationError("verification status must be passed or failed")
        safe_evidence = [_safe_json_value(item) for item in evidence]
        evidence_json = _json(safe_evidence)
        failure_reason = _safe_text(failure_reason, 4_000) if failure_reason else None
        with self.database.transaction() as connection:
            run = connection.execute("SELECT workspace_id, request_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["workspace_id"] != workspace_id:
                raise VerificationError("verification run is missing or belongs to another workspace")
            if task_id is not None:
                task = connection.execute("SELECT workspace_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                if task is None or task["workspace_id"] != workspace_id:
                    raise VerificationError("verification task is missing or belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO verification_attempts
                        (verification_attempt_id, workspace_id, task_id, run_id,
                         verifier_actor_id, contract_json, status, evidence_json,
                         failure_reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (attempt_id, workspace_id, task_id, run_id, verifier_actor_id,
                     _json(contract.to_dict()), status, evidence_json, failure_reason, _utc_now()),
                )
            except Exception as error:
                raise PersistenceConflictError("verification attempt already exists") from error
            row = connection.execute("SELECT * FROM verification_attempts WHERE verification_attempt_id = ?", (attempt_id,)).fetchone()
        assert row is not None
        return _attempt_row(row)

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        run_id = _id(run_id, "verification run id")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM verification_attempts WHERE run_id = ? ORDER BY created_at DESC, verification_attempt_id DESC",
                (run_id,),
            ).fetchall()
        return [_attempt_row(row) for row in rows]

    def latest_passed(self, run_id: str, contract_id: str) -> dict[str, Any] | None:
        run_id = _id(run_id, "verification run id")
        contract_id = _id(contract_id, "acceptance contract id")
        with self.database._lock:
            row = self.database._conn.execute(
                """
                SELECT * FROM verification_attempts
                WHERE run_id = ? AND status = 'passed'
                  AND json_extract(contract_json, '$.contract_id') = ?
                ORDER BY created_at DESC, verification_attempt_id DESC LIMIT 1
                """,
                (run_id, contract_id),
            ).fetchone()
        return _attempt_row(row) if row is not None else None


class VerificationService:
    """Run declared checks through injected checkers, never model-authored commands."""

    def __init__(self, work: WorkRepository, repository: VerificationRepository) -> None:
        self.work = work
        self.repository = repository

    def verify(
        self,
        contract: AcceptanceContract,
        *,
        run_id: str,
        verifier_actor_id: str,
        checkers: Mapping[str, Callable[[Mapping[str, Any]], Any]],
        human_approved: bool = False,
        attempt_id: str | None = None,
    ) -> VerificationAttempt:
        if not isinstance(contract, AcceptanceContract):
            raise VerificationError("acceptance contract is required")
        run = self.work.get_run(run_id)
        if run is None:
            raise VerificationError("verification run not found")
        request = self.work.get_request(run["request_id"])
        if request is None:
            raise VerificationError("verification request not found")
        verifier_actor_id = _id(verifier_actor_id, "verification actor id")
        with self.work.database._lock:
            actor = self.work.database._conn.execute(
                "SELECT actor_kind FROM actors WHERE actor_id = ?", (verifier_actor_id,)
            ).fetchone()
            builder = self.work.database._conn.execute(
                "SELECT actor_id FROM agent_installations WHERE agent_id = ?", (run["agent_id"],)
            ).fetchone()
        if actor is None:
            raise VerificationError("verifier actor not found")
        if builder is not None and builder["actor_id"] == verifier_actor_id:
            raise VerificationError("builder cannot verify its own run")
        if contract.human_review_required and (not human_approved or actor["actor_kind"] != ActorKind.HUMAN.value):
            return self._persist(
                contract, run, verifier_actor_id, "failed", (),
                "independent human review is required",
                attempt_id,
            )
        safe_checkers = checkers if isinstance(checkers, Mapping) else {}
        evidence: list[Mapping[str, Any]] = []
        failures: list[str] = []
        for check in contract.checks:
            kind = str(check["kind"])
            checker = safe_checkers.get(kind)
            if not callable(checker):
                failures.append(f"missing checker: {kind}")
                continue
            try:
                outcome = checker(check)
            except Exception as error:
                failures.append(f"checker failed: {kind}")
                evidence.append({"check_id": check["check_id"], "kind": kind, "passed": False, "error_type": type(error).__name__})
                continue
            passed, check_evidence = _checker_result(outcome)
            evidence.append({"check_id": check["check_id"], "kind": kind, "passed": passed, **check_evidence})
            if not passed:
                failures.append(f"check failed: {check['check_id']}")
        status = "passed" if not failures else "failed"
        return self._persist(contract, run, verifier_actor_id, status, evidence, "; ".join(failures) or None, attempt_id)

    def require_verified(self, contract: AcceptanceContract, run_id: str) -> None:
        if self.repository.latest_passed(run_id, contract.contract_id) is None:
            raise VerificationError("run has not passed its acceptance contract")

    def _persist(self, contract, run, verifier_actor_id, status, evidence, failure_reason, attempt_id):
        persisted = self.repository.create_attempt(
            attempt_id or new_domain_id("event"), run["workspace_id"], run["run_id"], verifier_actor_id,
            contract, task_id=self.work.get_request(run["request_id"]).get("task_id"),
            status=status, evidence=evidence, failure_reason=failure_reason,
        )
        return VerificationAttempt(
            verification_attempt_id=persisted["verification_attempt_id"], run_id=run["run_id"],
            task_id=persisted["task_id"], verifier_actor_id=verifier_actor_id,
            status=status, evidence=tuple(evidence), failure_reason=failure_reason,
        )


def _attempt_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["contract"] = json.loads(result.pop("contract_json") or "{}")
    result["evidence"] = json.loads(result.pop("evidence_json") or "[]")
    return result


def _checker_result(value: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(value, bool):
        return value, {}
    if isinstance(value, Mapping):
        passed = value.get("passed")
        if not isinstance(passed, bool):
            return False, {"error": "checker result lacks boolean passed"}
        safe = _safe_json_value(value)
        safe.pop("passed", None)
        return passed, safe
    return False, {"error": "checker result must be boolean or object"}


def _safe_json_value(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise VerificationError("verification data is not JSON serializable") from error
    text = json.dumps(redact_sensitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) > 20_000:
        raise VerificationError("verification data is too large")
    return json.loads(text)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 128:
        raise VerificationError(f"{label} is malformed")
    return value


def _optional_id(value: Any) -> str | None:
    return None if value is None or not str(value).strip() else _id(value, "optional id")


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
