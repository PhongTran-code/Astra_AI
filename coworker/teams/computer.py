"""Durable Computer plane for browser-capable Team agents.

The Computer boundary is deliberately provider-neutral.  A provider owns the
browser/desktop process; this module owns workspace identity, sessions, leases,
human takeover, and the audit-safe command path.  The existing sandbox remains
the execution boundary for files and commands and is explicitly not promoted
to a desktop security boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .contracts import ContractError, ResourceRef, new_domain_id
from .persistence import PersistenceConflictError, TeamDatabase


class ComputerError(RuntimeError):
    """A Computer operation cannot be performed under its durable contract."""


class ComputerNotReadyError(ComputerError):
    """The provider is not ready for the requested operation."""


class ScreenLeaseConflict(ComputerError):
    """Another actor owns the exclusive screen lease."""


class HumanTakeoverRequired(ComputerError):
    """A provider requires a human-only action before the run may continue."""

    pause_turn = True

    def __init__(self, takeover_request_id: str, message: str = "human takeover required") -> None:
        self.takeover_request_id = takeover_request_id
        super().__init__(message)


@dataclass(frozen=True)
class ComputerObservation:
    session_id: str
    url: str | None = None
    title: str | None = None
    screenshot_ref: str | None = None
    state: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "url": self.url,
            "title": self.title,
            "screenshot_ref": self.screenshot_ref,
            "state": dict(self.state or {}),
        }


class ComputerProvider(Protocol):
    """Provider SPI; implementations must not receive unrelated credentials."""

    provider_kind: str

    def provision(self, computer_id: str, provider_ref: str | None, capabilities: Mapping[str, Any]) -> None: ...
    def reconnect(self, computer_id: str, session_ref: str | None) -> str | None: ...
    def suspend(self, computer_id: str) -> None: ...
    def destroy(self, computer_id: str) -> None: ...
    def observe(self, session_ref: str) -> ComputerObservation: ...
    def screenshot(self, session_ref: str) -> bytes: ...
    def navigate(self, session_ref: str, url: str) -> ComputerObservation: ...
    def action(self, session_ref: str, action: Mapping[str, Any]) -> ComputerObservation: ...
    def transfer_file(self, session_ref: str, direction: str, path: str, content: bytes | None = None) -> Mapping[str, Any]: ...
    def checkpoint(self, session_ref: str) -> Mapping[str, Any]: ...
    def close_session(self, session_ref: str) -> None: ...


class LocalPlaywrightComputerProvider:
    """Lazy, local browser implementation of the Computer provider contract.

    Playwright is optional in Astra.  Importing Team or starting the API never
    launches a browser; the dependency and Chromium are required only when a
    Computer with this provider is provisioned.  Each Computer owns a browser
    context and each durable session maps to one page, so reconnect can reuse
    the persisted provider session reference without sharing cookies with an
    unrelated Computer.
    """

    provider_kind = "playwright_browser"

    def __init__(self, *, default_headless: bool = True) -> None:
        self.default_headless = bool(default_headless)
        self._lock = threading.RLock()
        self._playwright: Any | None = None
        self._browsers: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._page_computers: dict[str, str] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}

    def provision(
        self,
        computer_id: str,
        provider_ref: str | None,
        capabilities: Mapping[str, Any],
    ) -> None:
        del provider_ref
        with self._lock:
            if computer_id in self._contexts:
                return
            playwright = self._load_playwright()
            raw_capabilities = dict(capabilities or {})
            headless = raw_capabilities.get("headless", self.default_headless)
            if not isinstance(headless, bool):
                raise ComputerError("Playwright headless capability must be boolean")
            viewport = raw_capabilities.get("viewport") or {
                "width": 1280,
                "height": 900,
            }
            if (
                not isinstance(viewport, Mapping)
                or isinstance(viewport.get("width"), bool)
                or isinstance(viewport.get("height"), bool)
                or not isinstance(viewport.get("width"), int)
                or not isinstance(viewport.get("height"), int)
                or not 320 <= viewport["width"] <= 3840
                or not 240 <= viewport["height"] <= 2160
            ):
                raise ComputerError("Playwright viewport is invalid")
            try:
                browser = playwright.chromium.launch(headless=headless)
                context = browser.new_context(viewport=dict(viewport))
            except Exception as error:
                raise ComputerError(f"Playwright browser could not start: {type(error).__name__}") from error
            self._browsers[computer_id] = browser
            self._contexts[computer_id] = context
            self._capabilities[computer_id] = raw_capabilities

    def reconnect(self, computer_id: str, session_ref: str | None) -> str | None:
        with self._lock:
            context = self._contexts.get(computer_id)
            if context is None:
                raise ComputerNotReadyError("Playwright Computer is not provisioned")
            if session_ref:
                page = self._pages.get(session_ref)
                if page is not None and not page.is_closed():
                    return session_ref
                ref = str(session_ref)
            else:
                ref = f"{computer_id}:{uuid.uuid4().hex}"
            try:
                page = context.new_page()
            except Exception as error:
                raise ComputerError("Playwright session could not be opened") from error
            self._pages[ref] = page
            self._page_computers[ref] = computer_id
            return ref

    def suspend(self, computer_id: str) -> None:
        self._close_computer(computer_id)

    def destroy(self, computer_id: str) -> None:
        self._close_computer(computer_id)

    def observe(self, session_ref: str) -> ComputerObservation:
        with self._lock:
            page = self._page(session_ref)
            try:
                state = str(page.evaluate("document.readyState"))
                url = str(page.url or "") or None
                title = str(page.title() or "") or None
            except Exception as error:
                raise ComputerError("Playwright page could not be observed") from error
            return ComputerObservation(
                session_id=session_ref,
                url=url,
                title=title,
                state={"ready_state": state},
            )

    def screenshot(self, session_ref: str) -> bytes:
        with self._lock:
            page = self._page(session_ref)
            try:
                return bytes(page.screenshot(full_page=False))
            except Exception as error:
                raise ComputerError("Playwright screenshot failed") from error

    def navigate(self, session_ref: str, url: str) -> ComputerObservation:
        from ..web.guard import check_url

        blocked = check_url(url)
        if blocked:
            raise ComputerError(blocked)
        with self._lock:
            page = self._page(session_ref)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as error:
                raise ComputerError("Playwright navigation failed") from error
            return self.observe(session_ref)

    def action(self, session_ref: str, action: Mapping[str, Any]) -> ComputerObservation:
        if not isinstance(action, Mapping):
            raise ContractError("computer action must be an object")
        kind = str(action.get("kind") or "").strip().lower()
        if kind not in {"click", "type", "fill", "select", "press", "wait"}:
            raise ContractError("unsupported browser action kind")
        with self._lock:
            page = self._page(session_ref)
            try:
                if kind == "wait":
                    milliseconds = action.get("milliseconds", 500)
                    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
                        raise ContractError("browser wait milliseconds must be an integer")
                    page.wait_for_timeout(max(0, min(milliseconds, 30_000)))
                else:
                    target = _browser_target(page, action.get("target"))
                    if kind == "click":
                        target.click(timeout=10_000)
                    elif kind in {"type", "fill"}:
                        text = action.get("text")
                        if not isinstance(text, str) or len(text) > 50_000:
                            raise ContractError("browser text is required and bounded")
                        target.fill(text, timeout=10_000)
                    elif kind == "select":
                        value = action.get("value")
                        if not isinstance(value, str) or not value:
                            raise ContractError("browser select value is required")
                        target.select_option(value, timeout=10_000)
                    elif kind == "press":
                        key = action.get("key")
                        if not isinstance(key, str) or not key or len(key) > 64:
                            raise ContractError("browser key is required and bounded")
                        target.press(key, timeout=10_000)
            except (ContractError, ComputerError):
                raise
            except Exception as error:
                raise ComputerError("Playwright browser action failed") from error
            return self.observe(session_ref)

    def transfer_file(
        self,
        session_ref: str,
        direction: str,
        path: str,
        content: bytes | None = None,
    ) -> Mapping[str, Any]:
        del session_ref, direction, path, content
        raise ComputerError(
            "browser file transfer requires an explicit sandbox file broker"
        )

    def checkpoint(self, session_ref: str) -> Mapping[str, Any]:
        observation = self.observe(session_ref)
        return {
            "checkpoint_ref": _hash(
                f"{session_ref}:{observation.url or ''}:{observation.title or ''}"
            ),
            "session_id": session_ref,
            "url": observation.url,
            "title": observation.title,
            "state": dict(observation.state or {}),
        }

    def close_session(self, session_ref: str) -> None:
        with self._lock:
            page = self._pages.pop(str(session_ref), None)
            self._page_computers.pop(str(session_ref), None)
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            for computer_id in list(self._contexts):
                self._close_computer(computer_id)
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    def _load_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise ComputerError(
                "Playwright Computer requires the optional browser dependency; "
                "install `pip install 'coworker[browser]'` and Chromium"
            ) from error
        try:
            self._playwright = sync_playwright().start()
        except Exception as error:
            raise ComputerError("Playwright runtime could not start") from error
        return self._playwright

    def _page(self, session_ref: str) -> Any:
        session_ref = str(session_ref or "").strip()
        page = self._pages.get(session_ref)
        if page is None or page.is_closed():
            raise ComputerNotReadyError("Playwright session is offline; reconnect it first")
        return page

    def _close_computer(self, computer_id: str) -> None:
        context = self._contexts.pop(computer_id, None)
        browser = self._browsers.pop(computer_id, None)
        self._capabilities.pop(computer_id, None)
        for session_ref, owner in list(self._page_computers.items()):
            if owner == computer_id:
                self._page_computers.pop(session_ref, None)
                self._pages.pop(session_ref, None)
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


class ComputerRepository:
    """Persistence owner for Computer identity, sessions, screen leases, and takeover."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_computer(
        self,
        computer_id: str,
        workspace_id: str,
        agent_id: str,
        provider_kind: str,
        *,
        capabilities: Mapping[str, Any] | None = None,
        provider_ref: str | None = None,
    ) -> dict[str, Any]:
        computer_id = _id(computer_id, "computer id")
        workspace_id = _id(workspace_id, "computer workspace id")
        agent_id = _id(agent_id, "computer agent id")
        provider_kind = _kind(provider_kind, "computer provider kind")
        capabilities_json = _safe_json(capabilities or {}, "computer capabilities")
        now = _utc_now()
        with self.database.transaction() as connection:
            agent = connection.execute(
                "SELECT workspace_id FROM agent_installations WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if agent is None or agent["workspace_id"] != workspace_id:
                raise ContractError("computer agent is missing or belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO computers
                        (computer_id, workspace_id, agent_id, provider_kind, status,
                         capabilities_json, provider_ref, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'provisioning', ?, ?, ?, ?)
                    """,
                    (computer_id, workspace_id, agent_id, provider_kind, capabilities_json, provider_ref, now, now),
                )
            except Exception as error:
                raise PersistenceConflictError("computer identity already exists or agent already owns a computer") from error
            row = connection.execute("SELECT * FROM computers WHERE computer_id = ?", (computer_id,)).fetchone()
        assert row is not None
        return _computer_row(row)

    def get_computer(self, computer_id: str) -> dict[str, Any] | None:
        computer_id = _id(computer_id, "computer id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM computers WHERE computer_id = ?", (computer_id,)).fetchone()
        return _computer_row(row) if row is not None else None

    def get_for_agent(self, agent_id: str) -> dict[str, Any] | None:
        agent_id = _id(agent_id, "computer agent id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM computers WHERE agent_id = ?", (agent_id,)).fetchone()
        return _computer_row(row) if row is not None else None

    def get_takeover(self, takeover_id: str) -> dict[str, Any] | None:
        takeover_id = _id(takeover_id, "takeover id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM takeover_requests WHERE takeover_request_id = ?",
                (takeover_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_screen_lease(self, lease_id: str) -> dict[str, Any] | None:
        lease_id = _id(lease_id, "screen lease id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM screen_leases WHERE screen_lease_id = ?",
                (lease_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_for_workspace(self, workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        workspace_id = _id(workspace_id, "computer workspace id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("computer limit must be between 1 and 200")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM computers WHERE workspace_id = ? ORDER BY updated_at DESC, computer_id LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [_computer_row(row) for row in rows]

    def set_status(self, computer_id: str, status: str) -> dict[str, Any]:
        computer_id = _id(computer_id, "computer id")
        status = _kind(status, "computer status")
        if status not in {"provisioning", "ready", "suspended", "offline", "error", "destroyed"}:
            raise ContractError("unsupported computer status")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM computers WHERE computer_id = ?", (computer_id,)).fetchone()
            if row is None:
                raise ContractError("computer not found")
            current = row["status"]
            allowed = {
                "provisioning": {"ready", "error", "destroyed"},
                "ready": {"suspended", "offline", "error", "destroyed"},
                "suspended": {"ready", "offline", "error", "destroyed"},
                "offline": {"provisioning", "ready", "error", "destroyed"},
                "error": {"provisioning", "offline", "destroyed"},
                "destroyed": set(),
            }
            if status != current and status not in allowed.get(current, set()):
                raise ContractError(f"illegal computer transition {current} -> {status}")
            now = _utc_now()
            connection.execute(
                "UPDATE computers SET status = ?, updated_at = ?, destroyed_at = CASE WHEN ? = 'destroyed' THEN ? ELSE destroyed_at END WHERE computer_id = ?",
                (status, now, status, now, computer_id),
            )
            updated = connection.execute("SELECT * FROM computers WHERE computer_id = ?", (computer_id,)).fetchone()
        assert updated is not None
        return _computer_row(updated)

    def create_session(
        self,
        session_id: str,
        computer_id: str,
        workspace_id: str,
        agent_id: str,
        owner_actor_id: str,
        *,
        run_id: str | None = None,
        provider_session_ref: str | None = None,
    ) -> dict[str, Any]:
        values = (_id(session_id, "session id"), _id(computer_id, "session computer id"), _id(workspace_id, "session workspace id"), _id(agent_id, "session agent id"), _id(owner_actor_id, "session owner actor id"), _optional_id(run_id))
        now = _utc_now()
        with self.database.transaction() as connection:
            computer = connection.execute("SELECT workspace_id, agent_id, status FROM computers WHERE computer_id = ?", (values[1],)).fetchone()
            if computer is None or computer["workspace_id"] != values[2] or computer["agent_id"] != values[3]:
                raise ContractError("session computer identity does not match workspace and agent")
            if computer["status"] != "ready":
                raise ComputerNotReadyError("computer must be ready before opening a session")
            actor = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (values[2], values[4])).fetchone()
            if actor is None:
                raise ContractError("session owner is not an active workspace member")
            if values[5] is not None:
                run = connection.execute("SELECT workspace_id, agent_id FROM runs WHERE run_id = ?", (values[5],)).fetchone()
                if run is None or run["workspace_id"] != values[2] or run["agent_id"] != values[3]:
                    raise ContractError("session run does not match workspace and agent")
            try:
                connection.execute(
                    """
                    INSERT INTO computer_sessions
                        (session_id, computer_id, workspace_id, agent_id, run_id,
                         owner_actor_id, status, provider_session_ref, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (values[0], values[1], values[2], values[3], values[5], values[4], provider_session_ref, now, now),
                )
            except Exception as error:
                raise PersistenceConflictError("computer session already exists") from error
            row = connection.execute("SELECT * FROM computer_sessions WHERE session_id = ?", (values[0],)).fetchone()
        assert row is not None
        return dict(row)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = _id(session_id, "session id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM computer_sessions WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row is not None else None

    def set_session_status(self, session_id: str, status: str) -> dict[str, Any]:
        session_id = _id(session_id, "session id")
        status = _kind(status, "session status")
        if status not in {"active", "waiting_human", "suspended", "closed", "lost"}:
            raise ContractError("unsupported session status")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM computer_sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                raise ContractError("computer session not found")
            allowed = {"active": {"waiting_human", "suspended", "closed", "lost"}, "waiting_human": {"active", "closed", "lost"}, "suspended": {"active", "closed", "lost"}, "lost": {"active", "closed"}, "closed": set()}
            if status != row["status"] and status not in allowed.get(row["status"], set()):
                raise ContractError(f"illegal computer session transition {row['status']} -> {status}")
            now = _utc_now()
            connection.execute("UPDATE computer_sessions SET status = ?, updated_at = ?, closed_at = CASE WHEN ? = 'closed' THEN ? ELSE closed_at END WHERE session_id = ?", (status, now, status, now, session_id))
            updated = connection.execute("SELECT * FROM computer_sessions WHERE session_id = ?", (session_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def acquire_screen_lease(self, lease_id: str, session_id: str, workspace_id: str, holder_actor_id: str, *, lease_seconds: int = 60, now: str | None = None) -> dict[str, Any]:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ContractError("screen lease seconds must be positive")
        values = (_id(lease_id, "screen lease id"), _id(session_id, "screen lease session id"), _id(workspace_id, "screen lease workspace id"), _id(holder_actor_id, "screen lease holder id"))
        now = now or _utc_now()
        expires = _shift(now, lease_seconds)
        with self.database.transaction() as connection:
            session = connection.execute("SELECT workspace_id, status FROM computer_sessions WHERE session_id = ?", (values[1],)).fetchone()
            if session is None or session["workspace_id"] != values[2] or session["status"] not in {"active", "waiting_human"}:
                raise ComputerNotReadyError("session is not available for screen control")
            owner = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (values[2], values[3])).fetchone()
            if owner is None:
                raise ContractError("screen lease holder is not an active workspace member")
            existing = connection.execute("SELECT holder_actor_id, expires_at FROM screen_leases WHERE session_id = ? AND status = 'active'", (values[1],)).fetchone()
            if existing is not None and existing["expires_at"] > now and existing["holder_actor_id"] != values[3]:
                raise ScreenLeaseConflict("screen is already leased by another actor")
            if existing is not None:
                connection.execute("UPDATE screen_leases SET expires_at = ?, acquired_at = ? WHERE session_id = ? AND status = 'active'", (expires, now, values[1]))
                row = connection.execute("SELECT * FROM screen_leases WHERE session_id = ? AND status = 'active'", (values[1],)).fetchone()
            else:
                try:
                    connection.execute("INSERT INTO screen_leases (screen_lease_id, session_id, workspace_id, holder_actor_id, status, acquired_at, expires_at) VALUES (?, ?, ?, ?, 'active', ?, ?)", (*values, now, expires))
                except Exception as error:
                    raise ScreenLeaseConflict("screen is already leased") from error
                row = connection.execute("SELECT * FROM screen_leases WHERE screen_lease_id = ?", (values[0],)).fetchone()
        assert row is not None
        return dict(row)

    def release_screen_lease(self, lease_id: str, *, actor_id: str, now: str | None = None) -> dict[str, Any]:
        lease_id = _id(lease_id, "screen lease id")
        actor_id = _id(actor_id, "screen lease actor id")
        now = now or _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM screen_leases WHERE screen_lease_id = ?", (lease_id,)).fetchone()
            if row is None:
                raise ContractError("screen lease not found")
            if row["holder_actor_id"] != actor_id:
                raise ContractError("only the screen lease holder can release it")
            if row["status"] == "active":
                connection.execute("UPDATE screen_leases SET status = 'released', released_at = ? WHERE screen_lease_id = ?", (now, lease_id))
            updated = connection.execute("SELECT * FROM screen_leases WHERE screen_lease_id = ?", (lease_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def create_takeover(self, takeover_id: str, session_id: str, workspace_id: str, requested_by_actor_id: str, *, run_id: str | None = None, reason: str) -> dict[str, Any]:
        values = (_id(takeover_id, "takeover id"), _id(session_id, "takeover session id"), _id(workspace_id, "takeover workspace id"), _id(requested_by_actor_id, "takeover requester id"), _optional_id(run_id), str(reason or "").strip())
        if not values[5] or len(values[5]) > 500:
            raise ContractError("takeover reason is required and must be at most 500 characters")
        with self.database.transaction() as connection:
            session = connection.execute(
                "SELECT workspace_id, agent_id FROM computer_sessions WHERE session_id = ?",
                (values[1],),
            ).fetchone()
            if session is None or session["workspace_id"] != values[2]:
                raise ContractError("takeover session does not belong to workspace")
            if values[4] is not None:
                run = connection.execute(
                    "SELECT workspace_id, agent_id, status FROM runs WHERE run_id = ?",
                    (values[4],),
                ).fetchone()
                if (
                    run is None
                    or run["workspace_id"] != values[2]
                    or run["agent_id"] != session["agent_id"]
                    or run["status"] != "running"
                ):
                    raise ContractError("takeover run does not match the active session")
            owner = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (values[2], values[3])).fetchone()
            if owner is None:
                raise ContractError("takeover requester is not an active workspace member")
            existing = connection.execute("SELECT * FROM takeover_requests WHERE session_id = ? AND status = 'pending' ORDER BY created_at LIMIT 1", (values[1],)).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute("INSERT INTO takeover_requests (takeover_request_id, session_id, workspace_id, run_id, requested_by_actor_id, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)", (values[0], values[1], values[2], values[4], values[3], values[5], _utc_now()))
            row = connection.execute("SELECT * FROM takeover_requests WHERE takeover_request_id = ?", (values[0],)).fetchone()
        assert row is not None
        return dict(row)

    def decide_takeover(self, takeover_id: str, target: str, *, actor_id: str) -> dict[str, Any]:
        takeover_id = _id(takeover_id, "takeover id")
        actor_id = _id(actor_id, "takeover decision actor id")
        target = _kind(target, "takeover status")
        if target not in {"approved", "denied", "canceled"}:
            raise ContractError("takeover decision must be approved, denied, or canceled")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM takeover_requests WHERE takeover_request_id = ?", (takeover_id,)).fetchone()
            if row is None:
                raise ContractError("takeover request not found")
            member = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (row["workspace_id"], actor_id)).fetchone()
            if member is None:
                raise ContractError("takeover decision actor is not an active workspace member")
            if row["status"] != "pending":
                return dict(row)
            now = _utc_now()
            connection.execute("UPDATE takeover_requests SET status = ?, decided_at = ?, decided_by_actor_id = ? WHERE takeover_request_id = ?", (target, now, actor_id, takeover_id))
            if target in {"approved", "denied", "canceled"}:
                connection.execute("UPDATE computer_sessions SET status = 'active', updated_at = ? WHERE session_id = ? AND status = 'waiting_human'", (now, row["session_id"]))
            updated = connection.execute("SELECT * FROM takeover_requests WHERE takeover_request_id = ?", (takeover_id,)).fetchone()
        assert updated is not None
        return dict(updated)


class ComputerManager:
    """Provider adapter that keeps every computer action tied to a session."""

    def __init__(self, repository: ComputerRepository, provider: ComputerProvider, *, event_sink: Any | None = None) -> None:
        self.repository = repository
        self.provider = provider
        self.event_sink = event_sink

    def provision(self, computer_id: str) -> dict[str, Any]:
        computer = self._require_computer(computer_id)
        self.provider.provision(computer_id, computer.get("provider_ref"), computer.get("capabilities") or {})
        result = self.repository.set_status(computer_id, "ready")
        self._audit("computer.provisioned", computer, {})
        return result

    def suspend(self, computer_id: str) -> dict[str, Any]:
        computer = self._require_computer(computer_id)
        self.provider.suspend(computer_id)
        result = self.repository.set_status(computer_id, "suspended")
        self._audit("computer.suspended", computer, {})
        return result

    def destroy(self, computer_id: str) -> dict[str, Any]:
        computer = self._require_computer(computer_id)
        self.provider.destroy(computer_id)
        result = self.repository.set_status(computer_id, "destroyed")
        self._audit("computer.destroyed", computer, {})
        return result

    def reconnect(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        ref = self.provider.reconnect(session["computer_id"], session.get("provider_session_ref"))
        with self.repository.database.transaction() as connection:
            now = _utc_now()
            connection.execute("UPDATE computer_sessions SET provider_session_ref = ?, status = 'active', updated_at = ? WHERE session_id = ?", (ref, now, session_id))
            row = connection.execute("SELECT * FROM computer_sessions WHERE session_id = ?", (session_id,)).fetchone()
        assert row is not None
        result = dict(row)
        self._audit("computer.reconnected", self._require_computer(session["computer_id"]), {"session_id": session_id})
        return result

    def open_session(
        self,
        session_id: str,
        computer_id: str,
        workspace_id: str,
        agent_id: str,
        owner_actor_id: str,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.repository.get_session(session_id)
        if existing is not None:
            if (
                existing["computer_id"] != computer_id
                or existing["workspace_id"] != workspace_id
                or existing["agent_id"] != agent_id
                or existing.get("run_id") != run_id
            ):
                raise ContractError("Computer session identity cannot be rebound")
            return self.reconnect(session_id)
        session = self.repository.create_session(
            session_id,
            computer_id,
            workspace_id,
            agent_id,
            owner_actor_id,
            run_id=run_id,
        )
        try:
            return self.reconnect(session["session_id"])
        except Exception:
            try:
                self.repository.set_session_status(session["session_id"], "lost")
            except Exception:
                pass
            raise

    def close_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        ref = str(session.get("provider_session_ref") or session_id)
        closer = getattr(self.provider, "close_session", None)
        try:
            if callable(closer):
                closer(ref)
        finally:
            result = self.repository.set_session_status(session_id, "closed")
        self._audit(
            "computer.session.closed",
            self._require_computer(session["computer_id"]),
            {"session_id": session_id},
        )
        return result

    def observe(self, session_id: str) -> ComputerObservation:
        session = self._active_session(session_id)
        result = self.provider.observe(str(session.get("provider_session_ref") or session_id))
        self._audit("computer.observed", self._require_computer(session["computer_id"]), {"session_id": session_id})
        return result

    def screenshot(self, session_id: str) -> str:
        session = self._active_session(session_id)
        payload = self.provider.screenshot(str(session.get("provider_session_ref") or session_id))
        if not isinstance(payload, bytes) or len(payload) > 10 * 1024 * 1024:
            raise ComputerError("provider returned an invalid or oversized screenshot")
        self._audit("computer.screenshot", self._require_computer(session["computer_id"]), {"session_id": session_id, "bytes": len(payload)})
        return base64.b64encode(payload).decode("ascii")

    def navigate(self, session_id: str, url: str) -> ComputerObservation:
        session = self._active_session(session_id)
        url = str(url or "").strip()
        if not url or len(url) > 4096 or any(char in url for char in "\r\n\x00"):
            raise ContractError("browser URL is malformed")
        result = self.provider.navigate(str(session.get("provider_session_ref") or session_id), url)
        self._audit("computer.browser.navigate", self._require_computer(session["computer_id"]), {"session_id": session_id, "url_hash": _hash(url)})
        return result

    def action(self, session_id: str, action: Mapping[str, Any], *, lease_id: str | None = None, actor_id: str | None = None) -> ComputerObservation:
        session = self._active_session(session_id)
        if not isinstance(action, Mapping) or not str(action.get("kind") or "").strip():
            raise ContractError("computer action must declare a kind")
        if lease_id is not None:
            if actor_id is None:
                raise ContractError("screen lease actor is required")
            lease = self.repository.database._conn.execute("SELECT * FROM screen_leases WHERE screen_lease_id = ? AND status = 'active'", (lease_id,)).fetchone()
            if lease is None or lease["session_id"] != session_id or lease["holder_actor_id"] != actor_id or lease["expires_at"] <= _utc_now():
                raise ScreenLeaseConflict("valid screen lease is required")
        result = self.provider.action(str(session.get("provider_session_ref") or session_id), dict(action))
        self._audit("computer.action", self._require_computer(session["computer_id"]), {"session_id": session_id, "action_kind": str(action.get("kind"))})
        return result

    def checkpoint(self, session_id: str) -> dict[str, Any]:
        session = self._active_session(session_id)
        result = self.provider.checkpoint(str(session.get("provider_session_ref") or session_id))
        if not isinstance(result, Mapping):
            raise ComputerError("provider returned an invalid Computer checkpoint")
        self._audit(
            "computer.checkpointed",
            self._require_computer(session["computer_id"]),
            {"session_id": session_id},
        )
        return dict(result)

    def request_takeover(self, session_id: str, workspace_id: str, actor_id: str, *, run_id: str | None = None, reason: str) -> dict[str, Any]:
        result = self.repository.create_takeover(new_domain_id("event"), session_id, workspace_id, actor_id, run_id=run_id, reason=reason)
        self.repository.set_session_status(session_id, "waiting_human")
        self._audit("computer.takeover.requested", self._require_computer(self._require_session(session_id)["computer_id"]), {"session_id": session_id, "takeover_request_id": result["takeover_request_id"]})
        return result

    def decide_takeover(self, takeover_id: str, target: str, *, actor_id: str) -> dict[str, Any]:
        result = self.repository.decide_takeover(takeover_id, target, actor_id=actor_id)
        session = self._require_session(str(result["session_id"]))
        self._audit(
            "computer.takeover.decided",
            self._require_computer(session["computer_id"]),
            {
                "session_id": session["session_id"],
                "takeover_request_id": result["takeover_request_id"],
                "status": result["status"],
            },
        )
        return result

    def close(self) -> None:
        closer = getattr(self.provider, "close", None)
        if callable(closer):
            closer()

    def _require_computer(self, computer_id: str) -> dict[str, Any]:
        computer = self.repository.get_computer(computer_id)
        if computer is None:
            raise ContractError("computer not found")
        return computer

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.repository.get_session(session_id)
        if session is None:
            raise ContractError("computer session not found")
        return session

    def _active_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        if session["status"] != "active":
            raise ComputerNotReadyError("computer session is not active")
        return session

    def _audit(self, event_type: str, computer: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(event_type, ResourceRef("computer", str(computer["computer_id"])), dict(payload))


def _computer_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["capabilities"] = json.loads(result.pop("capabilities_json") or "{}")
    return result


def _safe_json(value: Any, label: str) -> str:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ContractError(f"{label} is not serializable") from error
    if len(encoded) > 20_000:
        raise ContractError(f"{label} is too large")
    return encoded


def _id(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 128 or any(char in value for char in "\x00\r\n"):
        raise ContractError(f"{label} is malformed")
    return value


def _optional_id(value: Any) -> str | None:
    return None if value is None or not str(value).strip() else _id(value, "optional id")


def _kind(value: Any, label: str) -> str:
    value = _id(value, label).lower()
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in value) or not value[0].isalpha():
        raise ContractError(f"{label} is malformed")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shift(value: str, seconds: int) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("timestamp is malformed") from error
    if parsed.tzinfo is None:
        raise ContractError("timestamp must include timezone")
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _browser_target(page: Any, raw_target: Any) -> Any:
    target = str(raw_target or "").strip()
    if not target or len(target) > 2_048 or any(char in target for char in "\x00\r\n"):
        raise ContractError("browser action target is malformed")
    if target.startswith("text="):
        return page.get_by_text(target[5:], exact=False).first
    if target.startswith("role="):
        role, _, name = target[5:].partition(":")
        if not role.strip():
            raise ContractError("browser role target is malformed")
        return page.get_by_role(role.strip(), name=name.strip() or None).first
    try:
        return page.locator(target).first
    except Exception as error:
        raise ContractError("browser selector is malformed") from error
