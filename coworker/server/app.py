"""FastAPI app — OpenAI-compatible endpoint + WS session API + REST.

The control plane every surface (GUI/IDE/messaging) rides on. The WS carries the engine
event stream and the approval channel; `/v1/chat/completions` is the OpenAI-compatible
proxy so any OpenAI-format client can use the runtime as a backend.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Origins allowed to talk to the local sidecar. It binds to 127.0.0.1, but a page in the
# user's own browser can still reach loopback — so without an origin gate, any website they
# visit could read `GET /v1/sessions` (CORS was `*`) and drive a session over the WS (which
# CORS never covers) into shell/file tools. We pin to the desktop webview's own origins
# (`tauri://localhost`, Windows' `http(s)://tauri.localhost`) and localhost dev/browser
# builds. Requests with NO Origin header (curl, native clients, tests, server-to-server) are
# allowed — the gate targets browsers, which always attach an unforgeable Origin.
_ALLOWED_ORIGIN_RE = re.compile(
    r"^(tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://0\.0\.0\.0(:\d+)?"
    r"|https?://tauri\.localhost"
    r"|file://"
    r"|null)$"
)


def _origin_allowed(origin: str | None, custom_origins: list[str] | None = None) -> bool:
    """True if a browser Origin may use the API. Missing Origin (non-browser) passes."""
    if origin is None:
        return True
    if _ALLOWED_ORIGIN_RE.match(origin):
        return True
    if custom_origins:
        for co in custom_origins:
            if co.strip() and origin.startswith(co.strip()):
                return True
    return False


# Caps on inbound WebSocket traffic. The loopback socket is unauthenticated (any local
# process can reach it), so bound frames, messages, and per-connection request rate before
# building model content or starting a turn.
_WS_MAX_FRAME_BYTES = 16 * 1024 * 1024
_WS_RATE_LIMIT_COUNT = 30
_WS_RATE_LIMIT_WINDOW_SECONDS = 10.0
_MAX_MESSAGE_TEXT_CHARS = 200_000
_MAX_ATTACHMENTS_BYTES = 15_000_000  # leaves JSON overhead below the 16 MiB frame cap

_VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
_VIDEO_STREAM_CHUNK_BYTES = 1024 * 1024


def _single_byte_range(
    range_header: str | None, size: int
) -> tuple[int, int, int] | None:
    """Return (start, end, status) for one HTTP byte range."""
    if not range_header:
        return (0, size - 1, 200)
    unit, separator, spec = range_header.partition("=")
    if separator != "=" or unit.strip().lower() != "bytes" or "," in spec:
        return None
    start_text, separator, end_text = spec.strip().partition("-")
    if separator != "-" or size <= 0:
        return None
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(size - suffix_length, 0)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
            if start < 0 or start >= size:
                return None
            end = min(end, size - 1)
    except ValueError:
        return None
    if start > end:
        return None
    return (start, end, 206)


def _stream_file_range(path: Path, start: int, end: int):
    remaining = end - start + 1
    with path.open("rb") as stream:
        stream.seek(start)
        while remaining > 0:
            chunk = stream.read(min(_VIDEO_STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def _json_value_size(value: Any) -> int:
    """Conservative UTF-8 size of parsed JSON without allocating another giant string."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_json_value_size(k) + _json_value_size(v) for k, v in value.items())
    if isinstance(value, list):
        return sum(_json_value_size(v) for v in value)
    return 8  # numbers, booleans, null, separators


# Brand colors for the connector badge riding the ✓ (UX-DECISIONS §30). The GUI owns the
# real logos; this page must render offline with zero assets, so a colored initial stands in.
_BRAND_COLORS = {
    "slack": "#4A154B",
    "github": "#24292f",
    "hubspot": "#ff7a59",
    "gmail": "#ea4335",
    "google_calendar": "#4285f4",
}


def _browser_page(
    title: str, detail: str, *, ok: bool = True, error: str = "", connector: str = ""
) -> str:
    """The page shown in the user's browser at the end of a loopback flow (sign-in or
    connector callback) — one branded card (UX-DECISIONS §30): OCW mark, ok/fail icon
    (the connector's initial rides the ✓), the friendly detail, and the raw error
    preserved on failures (it's the debugging breadcrumb). Inline CSS, light/dark via
    prefers-color-scheme, no external assets — it must render offline."""
    import html as _html

    badge = ""
    if ok and connector:
        color = _BRAND_COLORS.get(connector, "#3670b2")
        initial = _html.escape((connector[:1] or "?").upper())
        badge = f'<span class="mini" style="background:{color}">{initial}</span>'
    icon = (
        f'<div class="ico ok">✓{badge}</div>' if ok else '<div class="ico bad">✕</div>'
    )
    err = f'<div class="err">{_html.escape(error)}</div>' if error else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html.escape(title)} — Astra</title><style>"
        ":root{--paper:#f6f5f2;--panel:#fff;--line:#e4e2dc;--ink:#2c2c2a;--muted:#6f6e68;"
        "--faint:#a3a19a;--accent:#3670b2;--ok:#2e7d4f;--ok-soft:#e3f2e9;--bad:#b3423a;"
        "--bad-soft:#f8e7e5}"
        "@media(prefers-color-scheme:dark){:root{--paper:#191918;--panel:#232322;"
        "--line:#373633;--ink:#e8e6e1;--muted:#9d9b94;--faint:#6b6a64;--accent:#6ba3dd;"
        "--ok:#5cb884;--ok-soft:#20362a;--bad:#d97b74;--bad-soft:#3a2422}}"
        "body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;gap:18px;background:var(--paper);color:var(--ink);"
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px}'
        ".card{background:var(--panel);border:1px solid var(--line);border-radius:16px;"
        "padding:34px 32px 28px;max-width:320px;width:100%;text-align:center;"
        "box-shadow:0 10px 30px rgba(0,0,0,.06);box-sizing:border-box}"
        ".mark{display:flex;align-items:center;justify-content:center;gap:7px;margin-bottom:22px;"
        "font-size:13px;font-weight:650}"
        ".mark i{width:20px;height:20px;border-radius:6px;background:var(--accent);"
        "display:inline-block;position:relative}"
        ".mark i::after{content:'';position:absolute;inset:5px;border-radius:2px;"
        "background:conic-gradient(from 0deg,#fff 0 25%,transparent 0 50%,#fff 0 75%,transparent 0)}"
        ".ico{width:52px;height:52px;border-radius:50%;margin:0 auto 14px;display:flex;"
        "align-items:center;justify-content:center;font-size:24px;position:relative}"
        ".ico.ok{background:var(--ok-soft);color:var(--ok)}"
        ".ico.bad{background:var(--bad-soft);color:var(--bad)}"
        ".mini{position:absolute;right:-3px;bottom:-3px;width:22px;height:22px;border-radius:7px;"
        "display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;"
        "font-weight:700;border:2px solid var(--panel)}"
        "h1{font-size:17px;font-weight:650;margin:0 0 6px;letter-spacing:-.01em}"
        "p{font-size:12.5px;color:var(--muted);margin:0}"
        ".err{font-size:11.5px;color:var(--bad);background:var(--bad-soft);border-radius:8px;"
        "padding:7px 10px;margin-top:12px;text-align:left;word-break:break-word}"
        ".foot{font-size:10.5px;color:var(--faint)}"
        "</style></head><body>"
        '<div class="card"><div class="mark"><i></i>Astra</div>'
        f"{icon}<h1>{_html.escape(title)}</h1><p>{_html.escape(detail)}</p>{err}</div>"
        '<div class="foot">Served locally by Astra on your Mac</div>'
        "</body></html>"
    )


def _connector_title(name: str) -> str:
    """Display name for the loopback page — 'Slack connected', never 'slack connected'."""
    from ..connectors.descriptors import get_descriptor

    d = get_descriptor(name)
    return d.title if d else (name[:1].upper() + name[1:])


_CONNECT_FAILED_DETAIL = (
    "Something went wrong finishing this connection. "
    "Close this tab and try again from Astra."
)

from ..attachments import (
    MAX_ATTACHMENTS as _MAX_ATTACHMENTS,
    MAX_IMAGE_CHARS,
    MAX_PDF_CHARS,
    MAX_TEXT_CHARS,
    build_user_content,
)
from ..engine import ApprovalOutcome
from ..inbox import VIS_INBOX, VIS_INLINE, args_preview
from ..permissions import Mode
from ..providers import AssistantTurn
from .. import toolchain
from ..teams.model import AuthorityError as TeamsAuthorityError
from ..teams.model import BoardError as TeamsBoardError
from ..teams.model import BoardNotFoundError as TeamsBoardNotFoundError
from ..teams.contracts import ContractError as TeamContractError, WorkBudget, new_domain_id
from ..teams.api_contracts import ApiErrorCode, ApiErrorEnvelope, CommandEnvelope, QueryEnvelope
from ..teams.attachments import stored_name as team_attachment_stored_name
from ..teams import (
    ComputerError,
    ComputerNotReadyError,
    ContextBoundary,
    ContextScope,
    ResourceRef,
    ScreenLeaseConflict,
)
from ..branding import (
    ASTRA_AUTH_HEADER,
    ASTRA_WS_SUBPROTOCOL,
)
from .manager import SessionManager


def create_app(manager: SessionManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            live = (
                await manager.start_gateway()
            )  # start messaging listeners (if configured)
            if live:
                print(f"[coworker] messaging gateway live: {', '.join(live)}")
        except Exception:  # never let a bad connector stop the server
            import traceback

            traceback.print_exc()
        # Seed and warm the two read-only hosted MCP services after the event
        # loop is live. OAuth without an existing token remains needs_auth; no
        # startup path is allowed to open a browser.
        manager.ensure_auto_mcp_presets()
        manager.start_auto_mcp_connections()
        yield
        await manager.aclose()  # stop gateway + close MCP connections on shutdown

    app = FastAPI(title="Astra", version="0.0.0", lifespan=lifespan)
    api_token = os.environ.get("COWORKER_API_TOKEN", "")
    tokenless_paths = {
        "/v1/health",
        "/auth/callback",
        "/mcp/oauth/callback",
        "/oauth/callback",
        "/oauth/google/callback",
    }

    def _request_authenticated(request: Request) -> bool:
        provided = request.headers.get(ASTRA_AUTH_HEADER, "")
        # A native <video> element cannot attach a custom header. The video route accepts
        # the same sidecar token in its query string so the browser can issue range requests;
        # no other endpoint accepts query-string authentication.
        if not provided and (
            (
                request.url.path.startswith("/v1/sessions/")
                and request.url.path.endswith("/artifacts/video")
            )
            or request.url.path == "/v1/browser/proxy"
        ):
            provided = request.query_params.get("token", "")
        return bool(
            api_token
            and provided
            and secrets.compare_digest(provided, api_token)
        )

    def _websocket_protocol(ws: WebSocket) -> str | None:
        if not api_token:
            return None
        protocols = [
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        ]
        if not any(secrets.compare_digest(part, api_token) for part in protocols):
            return None
        if ASTRA_WS_SUBPROTOCOL in protocols:
            return ASTRA_WS_SUBPROTOCOL
        return None

    def _websocket_authenticated(ws: WebSocket) -> bool:
        if not api_token:
            return True
        protocols = [
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        ]
        return any(secrets.compare_digest(part, api_token) for part in protocols)

    @app.middleware("http")
    async def require_sidecar_token(request: Request, call_next):
        # Preflights carry the requested header name, not its value. CORS checks the
        # Origin; the actual state-changing request still must authenticate.
        if (
            not api_token
            or request.method == "OPTIONS"
            or request.url.path in tokenless_paths
            # `/v1/board` carries its own, stronger auth: per-actor board tokens
            # (identity + access), designed to be handed to external harnesses and
            # other machines — which can never hold the machine-local sidecar token.
            or request.url.path.startswith("/v1/board/")
            or _request_authenticated(request)
        ):
            return await call_next(request)
        return JSONResponse(
            {"error": "missing or invalid Astra sidecar token"},
            status_code=401,
        )

    app.add_middleware(
        CORSMiddleware,
        # Pinned to the desktop webview + localhost (see _ALLOWED_ORIGIN_RE): stops a random
        # website the user visits from reading local API responses cross-origin.
        allow_origin_regex=_ALLOWED_ORIGIN_RE.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.manager = manager

    @app.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        if api_token and not _request_authenticated(request):
            return {"status": "ok"}
        return {
            "status": "ok",
            "default_workspace": manager.default_workspace,
            "model": manager.model,
        }

    @app.get("/v1/agents")
    def agents() -> dict[str, Any]:
        return {"agents": manager.list_agents()}

    @app.get("/v1/personas")
    def personas() -> dict[str, Any]:
        from ..personas.registry import include_unshipped

        # `internal` tells the GUI it may show internal-build affordances (the
        # "Not in this release" group, the Gallery entry point).
        return {"personas": manager.personas.list_all(), "internal": include_unshipped()}

    @app.get("/v1/inbox")
    def inbox(session_id: str = "", state: str = "") -> dict[str, Any]:
        from dataclasses import asdict

        # The cross-session Inbox list shows only Unattended (inbox-visibility) items; a per-session
        # query returns inline ones too, so the answer-in-context card sees parked attended prompts.
        items = manager.inbox.list(
            session_id=session_id or None,
            state=state or None,
            visibility=None if session_id else VIS_INBOX,
        )
        # Enrich with the originating session's context so the Inbox is self-contained — the
        # "go to session" chip needs title/agent/workspace without depending on a (possibly stale)
        # client-side session list, and can link straight to it.
        out: list[dict[str, Any]] = []
        for i in items:
            d = asdict(i)
            rec = manager.session_store.load(i.session_id)
            if (
                rec is None
                and not session_id
                and i.state == "pending"
                and i.session_id not in manager._engines
            ):
                # Lazy cleanup for legacy orphans (sessions deleted before delete_session
                # started closing their items): an orphaned prompt can never be answered.
                # A LIVE engine without a record yet (brand-new session, first turn still
                # running) is NOT an orphan — hence the engine guard.
                manager.inbox.resolve_session(i.session_id)
                continue
            d["session_title"] = (rec.title if rec else None) or i.session_id
            d["session_agent"] = rec.agent if rec else None
            d["session_workspace"] = rec.workspace if rec else None
            d["session_exists"] = rec is not None
            out.append(d)
        return {"items": out}

    @app.post("/v1/inbox/{item_id}/resolve")
    async def resolve_inbox_item(item_id: str, body: dict) -> dict[str, Any]:
        # Idempotent + first-responder-wins: ok=False means it was already resolved elsewhere.
        # Routes through resolve_inbox so a restart-orphaned prompt durably resumes its turn.
        ok = await manager.resolve_inbox(item_id, str(body.get("resolution", "deny")))
        return {"ok": ok}

    @app.get("/v1/subscriptions")
    def subscriptions() -> dict[str, Any]:
        # Global view-only list: each (session → channel) subscription, enriched with the session's
        # title/agent and the channel its Inbox routes OUT to (so an inbound/outbound collision on
        # the same channel is visible).
        out: list[dict[str, Any]] = []
        for sub in manager.subscriptions.all():
            rec = manager.session_store.load(sub.session_id)
            agent = rec.agent if rec else ""
            routing = manager._routing_targets(sub.session_id, agent or "cowork")
            out.append(
                {
                    "session_id": sub.session_id,
                    "session_title": (rec.title if rec else None) or sub.session_id,
                    "agent": agent,
                    "channel": sub.channel,
                    # Display name from the channel buffer ("#ocw-test"), when any inbound
                    # message has carried one — the address stays the identifier.
                    "channel_name": manager.channel_buffer.name_for(sub.channel),
                    "routing_target": routing[0] if routing else None,
                    "collision": bool(routing and sub.channel in routing),
                }
            )
        return {"subscriptions": out}

    @app.get("/v1/channels/recent")
    def recent_channels() -> dict[str, Any]:
        # The picker's "recently-seen" source: channels the bot has received messages from.
        return {"channels": manager.channel_buffer.channels()}

    @app.get("/v1/unrouted")
    def unrouted() -> dict[str, Any]:
        # Dead-letter view: inbound messages with no destination + background-turn failures.
        return {"items": manager.unrouted.list()}

    @app.post("/v1/subscriptions")
    def subscribe(body: dict) -> dict[str, Any]:
        from ..subscriptions import resolve_channel

        session_id = str(body.get("session_id", "")).strip()
        raw = str(body.get("channel", ""))
        addr = resolve_channel(raw)
        if not session_id or not addr or ":" not in addr:
            if raw.strip().startswith("#"):
                # A bare #name can't be looked up locally — storing it literally would create a
                # subscription that never matches real traffic (resolve_channel returns "").
                return {
                    "ok": False,
                    "error": "Channel names can't be looked up — paste the channel ID "
                    "(channel name ▸ About) or the channel's Copy-link URL.",
                }
            return {"ok": False, "error": "need a session_id and a channel"}
        manager.subscriptions.subscribe(session_id, addr)
        return {"ok": True, "channel": addr}

    @app.post("/v1/subscriptions/remove")
    def unsubscribe(body: dict) -> dict[str, Any]:
        from ..subscriptions import resolve_channel

        session_id = str(body.get("session_id", "")).strip()
        addr = resolve_channel(str(body.get("channel", "")))
        removed = manager.subscriptions.unsubscribe(session_id, addr)
        return {"ok": True, "removed": removed}

    @app.get("/v1/inbox/reconcile")
    def reconcile_inbox(session_id: str) -> dict[str, Any]:
        # Called when a session resumes attended control (surface pending + recap inline).
        return manager.inbox.reconcile_on_resume(session_id)

    @app.get("/v1/inbox/routing")
    def inbox_routing() -> dict[str, Any]:
        return {"bindings": manager.inbox_routing.bindings()}

    @app.post("/v1/inbox/routing/binding")
    def set_inbox_binding(body: dict) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "binding needs a `name`"}
        return manager.set_inbox_binding(
            name,
            channel=body.get("channel") or None,
            target=str(body.get("target", "")),
        )

    @app.get("/v1/sessions/{session_id}/unattended")
    def get_unattended(session_id: str) -> dict[str, Any]:
        return {"unattended": manager.unattended.is_unattended(session_id)}

    @app.get("/v1/sessions/{session_id}/reviewer-stats")
    def get_reviewer_stats(session_id: str) -> dict[str, Any]:
        # Auto-Approve metering (§1.7): checks/verdicts/tokens from the durable audit rows.
        # Drives the composer's "Auto-Approve · N checks" badge and the mode-menu summary.
        return manager.audit_store.reviewer_stats(session_id)

    @app.post("/v1/sessions/{session_id}/unattended")
    def set_unattended(session_id: str, body: dict) -> dict[str, Any]:
        # The GUI gates the on-transition behind a one-tap confirm; the manager records the
        # transition either way, so the change is answerable from the audit store.
        return manager.set_unattended(session_id, bool(body.get("unattended")))

    @app.get("/v1/sessions/{session_id}/skills")
    def session_skills(session_id: str, workspace: str = "") -> dict[str, Any]:
        # The rail's Skills group + the composer popup both read this (SKILLS-SPEC §4.1).
        return manager.session_skills_view(session_id, workspace or None)

    @app.post("/v1/sessions/{session_id}/skills")
    def set_session_skill(session_id: str, body: dict) -> dict[str, Any]:
        # A session mute. `clear` drops the override (inherit again); otherwise explicit
        # on/off. Nothing on disk changes — Settings owns permanent state.
        body = body or {}
        skill = str(body.get("skill", "")).strip()
        if not skill:
            return {"ok": False, "error": "skill required"}
        if body.get("clear"):
            manager.session_skills.clear(session_id, skill)
        else:
            manager.session_skills.set(
                session_id, skill, bool(body.get("enabled", False))
            )
        return manager.session_skills_view(
            session_id, str(body.get("workspace", "")) or None
        )

    @app.get("/v1/sessions/{session_id}/connections")
    def session_connections(session_id: str, persona: str = "") -> dict[str, Any]:
        # `persona` is the GUI's hint for brand-new sessions (no record yet) — without it the
        # view resolves to the default persona and shows the wrong defaults/recommends.
        # §6: the Sources drawer payload — connected connectors w/ state + recommended + ⚠ count.
        return manager.session_connections_view(session_id, persona or None)

    @app.post("/v1/sessions/{session_id}/connections")
    def set_session_connection(session_id: str, body: dict) -> dict[str, Any]:
        # §6: a session override. `clear` drops the override (inherit the persona default again);
        # otherwise set an explicit on/off. Return the refreshed view so the drawer can re-render.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        if body.get("clear"):
            manager.session_connections.clear(session_id, connector)
        else:
            manager.session_connections.set(
                session_id, connector, bool(body.get("enabled", False))
            )
        persona = str(body.get("persona", "")) or None
        return {
            "ok": True,
            "connections": manager.session_connections_view(session_id, persona),
        }

    @app.post("/v1/personas/install")
    def install_persona(body: dict) -> dict[str, Any]:
        # Returns a consent summary per persona; they land disabled pending the user's approval
        # (then POST /v1/personas/{id} {enabled:true, surfaced:true}).
        reg = manager.personas
        try:
            if body.get("git_url"):
                summaries = reg.install_from_git(str(body["git_url"]))
            elif body.get("dir"):
                summaries = reg.install_from_dir(str(body["dir"]))
            elif body.get("zip_b64"):
                # Sharing v1 (OPE-7): a bundle zip — the export format — round-trips
                # through the same dir installer + consent path.
                try:
                    data = base64.b64decode(str(body["zip_b64"]), validate=True)
                except (ValueError, binascii.Error):
                    return {"ok": False, "error": "Invalid archive encoding."}
                summaries = reg.install_from_zip(
                    data, str(body.get("filename", ""))
                )
            elif body.get("gallery_slug"):
                # Gallery install = fetch the manifest markdown from the cloud
                # (sign-in required), verify its hash, then reuse the exact
                # same parser + consent path as a local/Git install. The
                # gallery never changes the trust model: no executable code,
                # lands disabled pending consent.
                import hashlib
                import tempfile

                from .. import cloud
                from ..config import load_config

                slug = str(body["gallery_slug"]).strip()
                manifest = cloud.gallery_manifest(manager.secrets, load_config(), slug)
                if manifest is None:
                    return {
                        "ok": False,
                        "error": "gallery requires cloud sign-in (or the cloud is unreachable)",
                    }
                markdown = manifest.get("manifest_markdown", "")
                digest = "sha256:" + hashlib.sha256(markdown.encode()).hexdigest()
                if (
                    manifest.get("manifest_hash")
                    and manifest["manifest_hash"] != digest
                ):
                    return {"ok": False, "error": "manifest hash mismatch"}
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / f"{slug}.md").write_text(markdown)
                    summaries = reg.install_from_dir(td)
                cloud.gallery_install_event(manager.secrets, load_config(), slug)
            else:
                return {
                    "ok": False,
                    "error": "provide a `dir`, `git_url`, `zip_b64`, or `gallery_slug`",
                }
        except Exception as e:  # surface manifest/clone errors to the caller
            return {"ok": False, "error": str(e)}
        return {"ok": True, "consent": summaries, "personas": reg.list_all()}

    @app.post("/v1/personas/{persona_id}/export")
    def export_persona(persona_id: str, body: dict) -> dict[str, Any]:
        # Sharing v1 (OPE-7): zip the persona's bundle into the chosen folder. The zip
        # is the import format — send it to a teammate, they import it from the picker.
        return manager.personas.export_persona(
            persona_id, str((body or {}).get("dir", ""))
        )

    @app.get("/v1/cloud/gallery/{slug}")
    def cloud_gallery_detail(slug: str) -> dict[str, Any]:
        """Solo page for one gallery coworker: publisher pitch + capabilities
        derived locally from the manifest (same parser as install)."""
        from .. import cloud
        from ..config import load_config

        body = cloud.gallery_detail(manager.secrets, load_config(), slug)
        if body is None:
            return {"ok": False, "error": "gallery requires cloud sign-in"}
        return body

    @app.get("/v1/cloud/gallery")
    def cloud_gallery() -> dict[str, Any]:
        """Gallery cards for the GUI. Signed out ⇒ ok:false (the gallery is a
        signed-in feature by design; local personas are unaffected)."""
        from .. import cloud
        from ..config import load_config

        body = cloud.gallery_list(manager.secrets, load_config())
        if body is None:
            return {
                "ok": False,
                "error": "gallery requires cloud sign-in",
                "personas": [],
            }
        return {"ok": True, "personas": body.get("personas", [])}

    @app.post("/v1/personas/{persona_id}")
    def update_persona(persona_id: str, body: dict) -> dict[str, Any]:
        reg = manager.personas
        archived = 0
        try:
            if "enabled" in body:
                # Disable archives the persona's sessions atomically (server-side, one
                # request) so any client gets the same semantic. See set_persona_enabled.
                archived = manager.set_persona_enabled(
                    persona_id, bool(body["enabled"])
                )["archived_sessions"]
            if "surfaced" in body:
                reg.set_surfaced(persona_id, bool(body["surfaced"]))
            if body.get("default"):
                reg.set_default(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": reg.list_all(), "archived_sessions": archived}

    @app.delete("/v1/personas/{persona_id}")
    def persona_delete(persona_id: str) -> dict[str, Any]:
        # Uninstall a non-builtin persona (snapshot dir + lifecycle state). Local
        # operation — works signed out, regardless of where the persona came from.
        try:
            manager.personas.uninstall(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "personas": manager.personas.list_all()}

    @app.get("/v1/personas/{persona_id}")
    def persona_detail(persona_id: str) -> dict[str, Any]:
        # §5 detail page: identity + capabilities + recommends(+connected) + default connections.
        detail = manager.persona_detail(persona_id)
        if detail is None:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return detail

    @app.get("/v1/personas/{persona_id}/media/{name}")
    def persona_media(persona_id: str, name: str) -> Any:
        # Screenshots from the persona bundle's media/ folder. The name is confined
        # to that folder: no separators, resolved path must stay inside it.
        from fastapi.responses import FileResponse, Response

        media_dir = manager.personas.media_dir(persona_id)
        if media_dir is None or "/" in name or "\\" in name or name.startswith("."):
            return Response(status_code=404)
        f = (media_dir / name).resolve()
        try:
            inside = f.is_relative_to(media_dir.resolve())
        except AttributeError:  # pragma: no cover — py<3.9 has no is_relative_to
            inside = str(f).startswith(str(media_dir.resolve()))
        if not inside or not f.is_file():
            return Response(status_code=404)
        return FileResponse(f)

    @app.post("/v1/personas/{persona_id}/enable")
    def persona_enable(persona_id: str, body: dict) -> dict[str, Any]:
        # Dedicated §5/§8 route; delegates to the same manager toggle as POST /v1/personas/{id}
        # (so disable archives the persona's sessions here too).
        try:
            manager.set_persona_enabled(
                persona_id, bool((body or {}).get("enabled", True))
            )
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": manager.personas.list_all()}

    @app.post("/v1/personas/{persona_id}/connections")
    def persona_set_connection(persona_id: str, body: dict) -> dict[str, Any]:
        # §5: flip a persona-default connector on/off; re-reads so the client can refresh.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        return manager.set_persona_connection(
            persona_id, connector, bool(body.get("enabled", False))
        )

    @app.get("/v1/skills")
    def skills(workspace: str = "") -> dict[str, Any]:
        return {"skills": manager.list_skills(workspace or None)}

    @app.post("/v1/skills")
    def create_skill(body: dict) -> dict[str, Any]:
        return manager.create_skill(body or {})

    @app.patch("/v1/skills/{name}")
    def update_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.update_skill(name, body or {})

    @app.delete("/v1/skills/{name}")
    def delete_skill(name: str, workspace: str = "") -> dict[str, Any]:
        return manager.delete_skill(name, workspace or None)

    @app.post("/v1/skills/{name}/move")
    def move_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.move_skill(name, body or {})

    @app.post("/v1/skills/{name}/reveal")
    def reveal_skill(name: str, body: dict) -> dict[str, Any]:
        # §6 "Show folder": open the skill's folder in the OS file manager (local machine).
        return manager.reveal_skill(name, str((body or {}).get("workspace", "")) or None)

    @app.post("/v1/skills/upload")
    def stage_skill_upload(body: dict) -> dict[str, Any]:
        # Stage → preview; nothing is installed until /upload/confirm (SKILLS-SPEC §4.2).
        data_b64 = str((body or {}).get("data_b64", ""))
        if not data_b64:
            return {"ok": False, "error": "No archive supplied."}
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "Invalid archive encoding."}
        return manager.stage_skill_upload(data, str((body or {}).get("filename", "")))

    @app.post("/v1/skills/upload/confirm")
    def confirm_skill_upload(body: dict) -> dict[str, Any]:
        return manager.confirm_skill_upload(body or {})

    @app.get("/v1/workspaces/recent")
    def recent_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.recent_workspaces()}

    @app.post("/v1/workspaces/open")
    def open_workspace(body: dict) -> dict[str, Any]:
        return manager.open_workspace(
            body.get("path", ""), create=bool(body.get("create"))
        )

    @app.get("/v1/workspaces/trusted")
    def trusted_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.trusted_workspaces()}

    @app.post("/v1/workspaces/trust")
    def set_workspace_trust(body: dict) -> dict[str, Any]:
        return manager.set_workspace_trust(
            str((body or {}).get("path", "")),
            trusted=bool((body or {}).get("trusted", False)),
        )

    @app.post("/v1/workspaces/temp")
    def provision_temp_workspace(body: dict) -> dict[str, Any]:
        # UX-029: a code-family session starting "in a temporary folder" — created only
        # at send time, with git ready. Knowledge families keep their auto-provisioned dir.
        return manager.provision_temp_workspace(
            str((body or {}).get("session_id", "")),
            git=bool((body or {}).get("git", True)),
        )

    @app.post("/v1/sessions/{session_id}/save-as-project")
    def save_session_as_project(session_id: str, body: dict) -> dict[str, Any]:
        # UX-029 "Save as project…": move the temporary folder somewhere real. The GUI
        # reconnects afterwards so the engine rebinds to the new path.
        return manager.save_temp_as_project(session_id, str((body or {}).get("path", "")))

    @app.post("/v1/workspaces/pick")
    async def pick_workspace() -> dict[str, Any]:
        # Native folder picker opened by the LOCAL sidecar (browser GUIs can't get absolute
        # paths from web file dialogs). Off the event loop: blocks until pick/cancel.
        return await asyncio.to_thread(manager.pick_native_folder)

    @app.get("/v1/sessions")
    def sessions(workspace: str | None = None) -> dict[str, Any]:
        return {"sessions": manager.list_sessions(workspace)}

    @app.get("/v1/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict[str, Any]:
        return {"messages": manager.session_messages(session_id)}

    @app.patch("/v1/sessions/{session_id}")
    def session_patch(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        if "pinned" in body or "archived" in body:
            return manager.set_session_flags(
                session_id,
                pinned=bool(body["pinned"]) if "pinned" in body else None,
                archived=bool(body["archived"]) if "archived" in body else None,
            )
        return manager.rename_session(session_id, str(body.get("title", "")))

    @app.delete("/v1/sessions/{session_id}")
    def session_delete(session_id: str) -> dict[str, Any]:
        return manager.delete_session(session_id)

    @app.get("/v1/sessions/{session_id}/roots")
    def session_roots(session_id: str) -> dict[str, Any]:
        return {"roots": manager.get_roots(session_id)}

    @app.post("/v1/sessions/{session_id}/roots")
    def session_add_root(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_root(
            session_id, str(body.get("path", "")), bool(body.get("writable", False))
        )

    @app.delete("/v1/sessions/{session_id}/roots")
    def session_remove_root(session_id: str, path: str) -> dict[str, Any]:
        return manager.remove_root(session_id, path)

    @app.get("/v1/browser/proxy")
    async def browser_proxy(url: str = "") -> Response:
        import html
        import httpx
        from fastapi.responses import HTMLResponse
        from ..web.guard import get_checked

        security_headers = {
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }

        if not url:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;padding:20px;'><h3>No URL specified</h3></body></html>",
                status_code=400,
                headers=security_headers,
            )
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"https://html.duckduckgo.com/html/?q={clean_url}"
        elif "duckduckgo.com" in clean_url and "/html" not in clean_url:
            clean_url = clean_url.replace("duckduckgo.com/?", "html.duckduckgo.com/html/?")

        def _fetch() -> tuple[int, str]:
            with httpx.Client(
                follow_redirects=False,
                timeout=12.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            ) as client:
                resp = get_checked(client, clean_url)
                return resp.status_code, resp.text

        try:
            status_code, body = await asyncio.to_thread(_fetch)
            if "<head>" in body:
                body = body.replace("<head>", f'<head><base href="{clean_url}">', 1)
            elif "<HEAD>" in body:
                body = body.replace("<HEAD>", f'<HEAD><base href="{clean_url}">', 1)
            return HTMLResponse(content=body, status_code=status_code, headers=security_headers)
        except PermissionError as exc:
            safe_err = html.escape(str(exc))
            return HTMLResponse(
                f"<html><body style='font-family:sans-serif;padding:24px;color:#333;'><h3>Blocked by URL safety policy</h3><p>{safe_err}</p></body></html>",
                status_code=403,
                headers=security_headers,
            )
        except Exception as exc:
            safe_err = html.escape(str(exc))
            return HTMLResponse(
                f"<html><body style='font-family:sans-serif;padding:24px;color:#333;'><h3>Failed to load page</h3><p>{safe_err}</p></body></html>",
                status_code=502,
                headers=security_headers,
            )

    @app.get("/v1/sessions/{session_id}/artifacts")
    def session_artifacts(session_id: str) -> dict[str, Any]:
        return {"artifacts": manager.list_artifacts(session_id)}

    @app.get("/v1/sessions/{session_id}/artifacts/read")
    def session_artifact_read(session_id: str, path: str) -> dict[str, Any]:
        return manager.read_artifact(session_id, path)

    @app.get("/v1/sessions/{session_id}/artifacts/video")
    def session_artifact_video(
        session_id: str, request: Request, path: str
    ) -> Response:
        target, err = manager.video_artifact_path(session_id, path)
        if target is None:
            return JSONResponse(
                {"ok": False, "error": err or "video not found"}, status_code=404
            )
        try:
            size = target.stat().st_size
        except OSError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

        selected = _single_byte_range(request.headers.get("range"), size)
        if selected is None:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        start, end, status = selected
        filename = target.name.replace('"', "")
        content_type = _VIDEO_MIME_TYPES.get(
            target.suffix.lower(), "application/octet-stream"
        )
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "Content-Length": str(max(0, end - start + 1)),
            "Content-Type": content_type,
            "Content-Disposition": f'inline; filename="{filename}"',
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            _stream_file_range(target, start, end),
            status_code=status,
            headers=headers,
            media_type=content_type,
        )

    @app.post("/v1/sessions/{session_id}/artifacts/reveal")
    def session_artifact_reveal(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.reveal_artifact(
            session_id, str(body.get("path", "")), str(body.get("mode", "reveal"))
        )

    # Agent teams (OPE-96): the session's board (workspace-keyed space) + journal
    # overview. Mutations act as the USER — the human side of the gates.
    @app.get("/v1/sessions/{session_id}/board")
    def session_board(session_id: str) -> dict[str, Any]:
        return manager.session_board(session_id)

    @app.get("/v1/sessions/{session_id}/board/item")
    def session_board_item(session_id: str, id: int) -> dict[str, Any]:
        return manager.board_item_detail(session_id, int(id))

    @app.get("/v1/sessions/{session_id}/board/attachment")
    def session_board_attachment(session_id: str, name: str):
        from fastapi.responses import Response

        try:
            data, mime = manager.board_attachment(session_id, name)
        except TeamsBoardError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
        return Response(
            content=data,
            media_type=mime,
        )

    @app.post("/v1/sessions/{session_id}/board/comment")
    def session_board_comment(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_comment(
            session_id, int(body.get("item", 0)), str(body.get("body", ""))
        )

    @app.post("/v1/sessions/{session_id}/board/transition")
    def session_board_transition(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_transition(
            session_id,
            int(body.get("item", 0)),
            str(body.get("to", "")),
            comment=str(body.get("comment", "")),
        )

    @app.get("/v1/teams/{team_id}/chat")
    def team_chat(team_id: str) -> dict[str, Any]:
        return manager.team_chat(team_id)

    @app.post("/v1/teams/{team_id}/chat")
    def team_chat_post(team_id: str, body: dict) -> dict[str, Any]:
        return manager.post_team_chat(team_id, str((body or {}).get("text", "")))

    @app.get("/v1/teams/journal")
    def teams_journal() -> dict[str, Any]:
        return {"cases": manager.journal_overview()}

    # Canonical Team surface.  The legacy `/v1/teams/{id}/chat` endpoints above
    # remain read-compatible, while new UI traffic uses workspace-scoped
    # conversations and the durable coordinator below.
    def _team_call(operation):
        try:
            return operation()
        except (TeamContractError, ComputerError) as error:
            message = str(error)
            lowered = message.lower()
            code = ApiErrorCode.INVALID_ARGUMENT
            if "not found" in lowered or "does not exist" in lowered:
                code = ApiErrorCode.NOT_FOUND
            elif "idempotency" in lowered:
                code = ApiErrorCode.IDEMPOTENCY_CONFLICT
            elif "permission" in lowered or "authority" in lowered or "denied" in lowered:
                code = ApiErrorCode.FORBIDDEN
            elif "approval" in lowered and "required" in lowered:
                code = ApiErrorCode.APPROVAL_REQUIRED
            elif "budget" in lowered or "quota" in lowered:
                code = ApiErrorCode.BUDGET_EXHAUSTED
            elif "conflict" in lowered or "already" in lowered or "stale" in lowered:
                code = ApiErrorCode.CONFLICT
            envelope = ApiErrorEnvelope(
                code=code,
                message=message,
                correlation_id="team-error",
                retryable=code in {ApiErrorCode.TEMPORARILY_UNAVAILABLE, ApiErrorCode.OFFLINE},
            )
            return JSONResponse({"error": message, "api_error": envelope.to_dict()}, status_code=400)

    def _team_agent_call(agent_id: str, operation):
        """Apply the Team workspace ACL before any agent-scoped lifecycle read/action."""

        def guarded():
            manager.team_service.get_agent(agent_id)
            return operation()

        return _team_call(guarded)

    def _serialize_team_submission(result) -> dict[str, Any]:
        routing = result.routing
        return {
            "message": result.message,
            "request": result.request,
            "deliveries": list(result.deliveries),
            "routing": (
                {
                    "conversation_id": routing.conversation_id,
                    "target_agent_ids": list(routing.target_agent_ids),
                    "reason": routing.reason.value,
                    "requires_confirmation": routing.requires_confirmation,
                }
                if routing is not None
                else None
            ),
        }

    def _serialize_connector_submission(result) -> dict[str, Any]:
        submission = result.submission
        return {
            "event": result.event,
            "request": submission.request if submission is not None else None,
            "deliveries": list(submission.deliveries) if submission is not None else [],
            "replayed": bool(result.replayed),
        }

    @app.get("/v1/team")
    def team_overview() -> dict[str, Any]:
        return _team_call(manager.team_service.overview)

    @app.post("/v1/team/commands", response_model=None)
    def team_command(body: dict) -> dict[str, Any] | JSONResponse:
        """Execute typed Team mutations while retaining legacy route compatibility."""

        def dispatch():
            raw = dict((body or {}).get("envelope") or body or {})
            envelope = CommandEnvelope.from_dict(raw)
            requester = manager.team_service.requester
            if envelope.workspace_id != manager.team_workspace_id or envelope.requester != requester:
                raise TeamContractError("command requester or workspace is not the authenticated Team principal")
            payload = dict(envelope.payload)
            command = envelope.command_type
            if command == "conversation.message.create":
                result = manager.team_service.send_message(
                    str(payload.get("conversation_id") or ""),
                    body=str(payload.get("body") or ""),
                    message_id=payload.get("message_id"),
                    client_request_id=envelope.idempotency_key,
                    mention_actor_ids=tuple(str(value) for value in (payload.get("mention_actor_ids") or ())),
                    at_all=bool(payload.get("at_all", False)),
                    fanout_confirmed=bool(payload.get("fanout_confirmed", False)),
                    thread_id=payload.get("thread_id"),
                    parent_message_id=payload.get("parent_message_id"),
                    quote_message_id=payload.get("quote_message_id"),
                )
                manager.kick_team_tick()
                return {"command": envelope.to_dict(), "result": _serialize_team_submission(result)}
            if command == "task.create":
                return {"command": envelope.to_dict(), "result": manager.team_service.create_task(
                    title=str(payload.get("title") or ""),
                    description=str(payload.get("description") or ""),
                    acceptance_criteria=str(payload.get("acceptance_criteria") or ""),
                    owner_agent_id=payload.get("owner_agent_id"),
                    reviewer_actor_id=payload.get("reviewer_actor_id"),
                    priority=int(payload.get("priority", 0)),
                )}
            if command == "task.transition":
                return {"command": envelope.to_dict(), "result": manager.team_service.transition_task(
                    str(payload.get("task_id") or ""), str(payload.get("status") or ""),
                )}
            if command == "approval.decide":
                result = manager.team_service.decide_approval(
                    str(payload.get("approval_id") or ""), str(payload.get("status") or ""),
                )
                manager.kick_team_tick()
                return {"command": envelope.to_dict(), "result": result}
            if command == "run.cancel":
                return {"command": envelope.to_dict(), "result": manager.cancel_canonical_team_run(
                    str(payload.get("run_id") or ""), reason=str(payload.get("reason") or "canceled by requester"),
                )}
            raise TeamContractError(f"unsupported Team command type: {command}")

        return _team_call(dispatch)

    @app.get("/v1/team/queries", response_model=None)
    def team_query(
        query_type: str,
        query_id: str,
        correlation_id: str,
        filters: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any] | JSONResponse:
        """Execute typed Team queries; returned state remains REST-authoritative."""

        def dispatch():
            parsed_filters: dict[str, Any] = {}
            if filters:
                try:
                    decoded = json.loads(filters)
                except json.JSONDecodeError as error:
                    raise TeamContractError("query filters must be valid JSON") from error
                if not isinstance(decoded, dict):
                    raise TeamContractError("query filters must be an object")
                parsed_filters = decoded
            envelope = QueryEnvelope(
                query_id=query_id,
                query_type=query_type,
                workspace_id=manager.team_workspace_id,
                requester=manager.team_service.requester,
                correlation_id=correlation_id,
                filters=parsed_filters,
                cursor=cursor,
                limit=limit,
            )
            if envelope.query_type == "team.overview":
                result = manager.team_service.overview()
            elif envelope.query_type == "tasks.list":
                result = manager.team_service.list_tasks(
                    statuses=tuple(str(item) for item in (parsed_filters.get("statuses") or ())),
                    cursor=envelope.cursor, limit=envelope.limit,
                )
            elif envelope.query_type == "runs.list":
                result = {"runs": manager.team_service.list_runs(
                    agent_id=parsed_filters.get("agent_id"), task_id=parsed_filters.get("task_id"),
                    conversation_id=parsed_filters.get("conversation_id"),
                    statuses=tuple(str(item) for item in (parsed_filters.get("statuses") or ())), limit=envelope.limit,
                )}
            elif envelope.query_type == "events.list":
                result = manager.team_service.list_events(
                    after_sequence=int(parsed_filters.get("after_sequence", 0)), limit=envelope.limit,
                )
            else:
                raise TeamContractError(f"unsupported Team query type: {envelope.query_type}")
            return {"query": envelope.to_dict(), "result": result}

        return _team_call(dispatch)

    @app.get("/v1/team/observability", response_model=None)
    def team_observability(run_id: Optional[str] = None, limit: int = 200) -> dict[str, Any] | JSONResponse:
        """Return durable Team metrics, or a bounded redacted run trace."""

        def read():
            manager.team_service._require_workspace_member()
            if run_id:
                try:
                    return manager.team_observability.trace(
                        manager.team_workspace_id, run_id, limit=limit
                    )
                except LookupError as error:
                    raise TeamContractError(str(error)) from error
            try:
                return manager.team_observability.metrics(manager.team_workspace_id)
            except ValueError as error:
                raise TeamContractError(str(error)) from error

        return _team_call(read)

    # Computer is a durable Team resource.  The provider is deliberately behind
    # ComputerManager so browser actions cannot bypass workspace ACLs, sessions,
    # leases, or the audit event sink.
    def _team_computer(computer_id: str) -> dict[str, Any]:
        manager.team_service._require_workspace_member()
        computer = manager.team_computer_repository.get_computer(computer_id)
        if computer is None or computer["workspace_id"] != manager.team_workspace_id:
            raise TeamContractError("computer not found")
        return computer

    def _team_computer_manage() -> None:
        manager.team_service._require_workspace_action("agent.manage")

    def _team_computer_for_manage(computer_id: str) -> dict[str, Any]:
        _team_computer_manage()
        return _team_computer(computer_id)

    def _team_computer_session(session_id: str) -> dict[str, Any]:
        manager.team_service._require_workspace_member()
        session = manager.team_computer_repository.get_session(session_id)
        if session is None or session["workspace_id"] != manager.team_workspace_id:
            raise TeamContractError("computer session not found")
        return session

    def _team_observation(value: Any) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, dict):
            return dict(value)
        raise TeamContractError("computer provider returned an invalid observation")

    @app.get("/v1/team/computers", response_model=None)
    def team_computers(limit: int = 100) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: {
                "computers": manager.team_computer_repository.list_for_workspace(
                    manager.team_workspace_id, limit=limit
                )
            }
        )

    @app.post("/v1/team/computers", response_model=None)
    def team_computer_create(body: dict) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def create():
            _team_computer_manage()
            provider_kind = str(
                body.get("provider_kind") or manager.team_computer_provider.provider_kind
            ).strip().lower()
            if provider_kind != manager.team_computer_provider.provider_kind:
                raise TeamContractError("unsupported local Computer provider")
            computer_id = str(body.get("computer_id") or f"computer_{uuid.uuid4().hex}")
            return manager.team_computer_repository.create_computer(
                computer_id,
                manager.team_workspace_id,
                str(body.get("agent_id") or ""),
                provider_kind,
                capabilities=body.get("capabilities") or {},
                provider_ref=body.get("provider_ref"),
            )

        return _team_call(create)

    @app.get("/v1/team/computers/{computer_id}", response_model=None)
    def team_computer_get(computer_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(lambda: _team_computer(computer_id))

    @app.post("/v1/team/computers/{computer_id}/provision", response_model=None)
    def team_computer_provision(computer_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: (
                _team_computer_for_manage(computer_id),
                manager.team_computers.provision(computer_id),
            )[1]
        )

    @app.post("/v1/team/computers/{computer_id}/suspend", response_model=None)
    def team_computer_suspend(computer_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: (
                _team_computer_for_manage(computer_id),
                manager.team_computers.suspend(computer_id),
            )[1]
        )

    @app.post("/v1/team/computers/{computer_id}/destroy", response_model=None)
    def team_computer_destroy(computer_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: (
                _team_computer_for_manage(computer_id),
                manager.team_computers.destroy(computer_id),
            )[1]
        )

    @app.post("/v1/team/computers/{computer_id}/sessions", response_model=None)
    def team_computer_session_create(
        computer_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def create():
            computer = _team_computer(computer_id)
            agent_id = str(body.get("agent_id") or computer["agent_id"])
            session_id = str(body.get("session_id") or f"session_{uuid.uuid4().hex}")
            session = manager.team_computer_repository.create_session(
                session_id,
                computer_id,
                manager.team_workspace_id,
                agent_id,
                manager.team_actor_id,
                run_id=body.get("run_id"),
            )
            try:
                return manager.team_computers.reconnect(session["session_id"])
            except Exception:
                # A durable session that cannot attach to its provider is marked
                # lost so restart/recovery can distinguish it from an active page.
                manager.team_computer_repository.set_session_status(
                    session["session_id"], "lost"
                )
                raise

        return _team_call(create)

    @app.get("/v1/team/computers/sessions/{session_id}", response_model=None)
    def team_computer_session_get(session_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(lambda: _team_computer_session(session_id))

    @app.post("/v1/team/computers/sessions/{session_id}/reconnect", response_model=None)
    def team_computer_session_reconnect(session_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: (
                _team_computer_session(session_id),
                manager.team_computers.reconnect(session_id),
            )[1]
        )

    @app.get("/v1/team/computers/sessions/{session_id}/observe", response_model=None)
    def team_computer_session_observe(session_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: _team_observation(
                (_team_computer_session(session_id), manager.team_computers.observe(session_id))[1]
            )
        )

    @app.get("/v1/team/computers/sessions/{session_id}/screenshot", response_model=None)
    def team_computer_session_screenshot(session_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(
            lambda: {
                "session_id": session_id,
                "data_b64": manager.team_computers.screenshot(
                    _team_computer_session(session_id)["session_id"]
                ),
            }
        )

    @app.post("/v1/team/computers/sessions/{session_id}/navigate", response_model=None)
    def team_computer_session_navigate(
        session_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}
        return _team_call(
            lambda: _team_observation(
                manager.team_computers.navigate(
                    _team_computer_session(session_id)["session_id"],
                    str(body.get("url") or ""),
                )
            )
        )

    @app.post("/v1/team/computers/sessions/{session_id}/action", response_model=None)
    def team_computer_session_action(
        session_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def action():
            _team_computer_session(session_id)
            lease_id = body.get("lease_id")
            if not lease_id:
                raise ScreenLeaseConflict("screen lease is required for browser actions")
            lease = manager.team_computer_repository.get_screen_lease(str(lease_id))
            if (
                lease is None
                or lease["workspace_id"] != manager.team_workspace_id
                or lease["session_id"] != session_id
            ):
                raise TeamContractError("screen lease not found")
            return _team_observation(
                manager.team_computers.action(
                    session_id,
                    body.get("action") or body,
                    lease_id=str(lease_id) if lease_id else None,
                    actor_id=manager.team_actor_id,
                )
            )

        return _team_call(action)

    @app.post("/v1/team/computers/sessions/{session_id}/lease", response_model=None)
    def team_computer_lease_acquire(
        session_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def acquire():
            _team_computer_session(session_id)
            lease_seconds = body.get("lease_seconds", 60)
            lease_id = str(body.get("lease_id") or f"lease_{uuid.uuid4().hex}")
            return manager.team_computer_repository.acquire_screen_lease(
                lease_id,
                session_id,
                manager.team_workspace_id,
                manager.team_actor_id,
                lease_seconds=lease_seconds,
            )

        return _team_call(acquire)

    @app.delete("/v1/team/computers/sessions/{session_id}/lease/{lease_id}", response_model=None)
    def team_computer_lease_release(
        session_id: str, lease_id: str
    ) -> dict[str, Any] | JSONResponse:
        def release():
            _team_computer_session(session_id)
            lease = manager.team_computer_repository.get_screen_lease(lease_id)
            if lease is None or lease["session_id"] != session_id:
                raise TeamContractError("screen lease not found")
            return manager.team_computer_repository.release_screen_lease(
                lease_id, actor_id=manager.team_actor_id
            )

        return _team_call(release)

    @app.post("/v1/team/computers/sessions/{session_id}/takeover", response_model=None)
    def team_computer_takeover(
        session_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}
        return _team_call(
            lambda: manager.team_computers.request_takeover(
                _team_computer_session(session_id)["session_id"],
                manager.team_workspace_id,
                manager.team_actor_id,
                run_id=body.get("run_id"),
                reason=str(body.get("reason") or ""),
            )
        )

    @app.post("/v1/team/takeovers/{takeover_id}/decide", response_model=None)
    def team_computer_takeover_decide(
        takeover_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def decide():
            manager.team_service._require_workspace_action("approval.decide")
            takeover = manager.team_computer_repository.get_takeover(takeover_id)
            if takeover is None or takeover["workspace_id"] != manager.team_workspace_id:
                raise TeamContractError("takeover request not found")
            result = manager.team_computers.decide_takeover(
                takeover_id,
                str(body.get("status") or ""),
                actor_id=manager.team_actor_id,
            )
            resolved = manager.team_coordinator.resolve_takeover(result)
            if resolved is not None:
                result = {**result, "run": resolved.get("run"), "delivery": resolved.get("delivery")}
            if result.get("status") == "approved":
                manager.kick_team_tick()
            return result

        return _team_call(decide)

    def _team_routine(routine_id: str) -> dict[str, Any]:
        manager.team_service._require_workspace_member()
        routine = manager.team_routines.get(routine_id)
        if routine is None or routine["workspace_id"] != manager.team_workspace_id:
            raise TeamContractError("routine not found")
        return routine

    def _team_routine_context(routine: Mapping[str, Any]) -> dict[str, Any]:
        return {
            str(agent_id): manager.team_service._permission_context(str(agent_id))
            for agent_id in routine["target_agent_ids"]
        }

    def _serialize_routine_trigger(result: Any) -> dict[str, Any]:
        submission = result.submission
        return {
            "occurrence": dict(result.occurrence),
            "replayed": bool(result.replayed),
            "request": submission.request if submission is not None else None,
            "deliveries": list(submission.deliveries) if submission is not None else [],
        }

    @app.get("/v1/team/routines", response_model=None)
    def team_routines(
        status: Optional[str] = "active", limit: int = 200
    ) -> dict[str, Any] | JSONResponse:
        def list_routines():
            manager.team_service._require_workspace_member()
            normalized = None if status in {None, "", "all"} else status
            return {
                "routines": manager.team_routines.list_for_workspace(
                    manager.team_workspace_id, status=normalized, limit=limit
                )
            }

        return _team_call(list_routines)

    @app.post("/v1/team/routines", response_model=None)
    def team_routine_create(body: dict) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def create():
            manager.team_service._require_workspace_action("task.create")
            return manager.team_routines.create(
                str(body.get("routine_id") or new_domain_id("automation")),
                manager.team_workspace_id,
                manager.team_actor_id,
                name=str(body.get("name") or ""),
                trigger_kind=str(body.get("trigger_kind") or "manual"),
                target_agent_ids=tuple(
                    str(value) for value in (body.get("target_agent_ids") or ())
                ),
                instruction=str(body.get("instruction") or ""),
                schedule=body.get("schedule") or {},
                timezone_name=str(body.get("timezone") or "UTC"),
                conversation_id=body.get("conversation_id"),
                task_id=body.get("task_id"),
                expected_output=str(body.get("expected_output") or ""),
                budget=body.get("budget") or {},
                approval_policy=body.get("approval_policy") or {},
            )

        return _team_call(create)

    @app.get("/v1/team/routines/{routine_id}", response_model=None)
    def team_routine_get(routine_id: str) -> dict[str, Any] | JSONResponse:
        return _team_call(lambda: _team_routine(routine_id))

    @app.post("/v1/team/routines/{routine_id}/transition", response_model=None)
    def team_routine_transition(
        routine_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def transition():
            manager.team_service._require_workspace_action("task.transition")
            _team_routine(routine_id)
            return manager.team_routines.set_status(
                routine_id, str(body.get("status") or "")
            )

        return _team_call(transition)

    @app.post("/v1/team/routines/{routine_id}/trigger", response_model=None)
    def team_routine_trigger(
        routine_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def trigger():
            manager.team_service._require_workspace_action("run.execute")
            routine = _team_routine(routine_id)
            occurrence_key = str(
                body.get("occurrence_key") or f"manual:{uuid.uuid4().hex}"
            )
            result = manager.team_routine_service.trigger(
                routine_id,
                occurrence_key=occurrence_key,
                trigger_kind="manual",
                context_by_agent=_team_routine_context(routine),
                instruction_suffix=str(body.get("instruction_suffix") or ""),
            )
            if result.submission is not None:
                manager.kick_team_tick()
            return _serialize_routine_trigger(result)

        return _team_call(trigger)

    @app.get("/v1/team/presence")
    def team_presence() -> dict[str, Any]:
        return _team_call(lambda: {"agents": manager.team_service.list_agent_presence()})

    @app.get("/v1/team/agents")
    def team_agents() -> dict[str, Any]:
        return _team_call(lambda: {"agents": manager.team_service.list_agents()})

    @app.post("/v1/team/agents")
    def team_agent_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_agent_lifecycle.create(
                workspace_id=manager.team_workspace_id,
                requester=manager.team_service.requester,
                display_name=str(body.get("display_name", "")),
                handle=str(body.get("handle", "")),
                instructions=str(body.get("instructions", "")),
                base_role=str(body.get("base_role", "builder")),
                description=str(body.get("description", "")),
                avatar_id=body.get("avatar_id"),
                model_policy=body.get("model_policy") or {},
                sandbox_policy=body.get("sandbox_policy") or {},
                memory_policy=body.get("memory_policy") or {},
                reply_policy=body.get("reply_policy") or {},
                budget_policy=body.get("budget_policy") or {},
                approval_policy=body.get("approval_policy") or {},
                capabilities=tuple(body.get("capabilities") or ()),
                max_concurrency=int(body.get("max_concurrency", 1)),
            )
        )

    @app.patch("/v1/team/agents/{agent_id}")
    def team_agent_edit(agent_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_agent_call(
            agent_id,
            lambda: manager.team_agent_lifecycle.edit(
                agent_id,
                requester=manager.team_service.requester,
                instructions=(
                    str(body["instructions"])
                    if "instructions" in body
                    else None
                ),
                display_name=body.get("display_name"),
                handle=body.get("handle"),
                description=body.get("description"),
                avatar_id=body.get("avatar_id"),
                model_policy=body.get("model_policy") if "model_policy" in body else None,
                sandbox_policy=body.get("sandbox_policy") if "sandbox_policy" in body else None,
                memory_policy=body.get("memory_policy") if "memory_policy" in body else None,
                reply_policy=body.get("reply_policy") if "reply_policy" in body else None,
                budget_policy=body.get("budget_policy") if "budget_policy" in body else None,
                approval_policy=body.get("approval_policy") if "approval_policy" in body else None,
                capabilities=tuple(body["capabilities"]) if "capabilities" in body else None,
            )
        )

    @app.post("/v1/team/agents/{agent_id}/clone")
    def team_agent_clone(agent_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_agent_call(
            agent_id,
            lambda: manager.team_agent_lifecycle.clone(
                agent_id,
                requester=manager.team_service.requester,
                display_name=body.get("display_name"),
                handle=body.get("handle"),
            )
        )

    @app.get("/v1/team/agents/{agent_id}/export")
    def team_agent_export(agent_id: str) -> dict[str, Any]:
        return _team_agent_call(agent_id, lambda: manager.team_agent_lifecycle.export(agent_id))

    @app.post("/v1/team/agents/import")
    def team_agent_import(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_agent_lifecycle.import_package(
                body.get("package") or {},
                workspace_id=manager.team_workspace_id,
                requester=manager.team_service.requester,
                human_approved=bool(body.get("human_approved", False)),
                handle=body.get("handle"),
            ).__dict__
        )

    @app.post("/v1/team/agents/{agent_id}/playground")
    def team_agent_playground(agent_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        command = body.get("command") or []
        return _team_agent_call(
            agent_id,
            lambda: manager.team_agent_lifecycle.test_playground(
                agent_id,
                tuple(str(part) for part in command),
                environment=body.get("environment") or {},
                resource_policy=body.get("resource_policy") or {},
            ).__dict__
        )

    @app.get("/v1/team/agents/{agent_id}/sandbox/health")
    def team_agent_sandbox_health(agent_id: str) -> dict[str, Any]:
        return _team_agent_call(agent_id, lambda: manager.team_agent_lifecycle.sandbox_health(agent_id))

    @app.post("/v1/team/agents/{agent_id}/{action}")
    def team_agent_action(agent_id: str, action: str, body: Optional[dict] = None) -> dict[str, Any]:
        body = body or {}
        if action == "clone":
            return _team_agent_call(
                agent_id,
                lambda: manager.team_agent_lifecycle.clone(
                    agent_id,
                    requester=manager.team_service.requester,
                    display_name=body.get("display_name"),
                    handle=body.get("handle"),
                )
            )
        if action == "playground":
            return _team_agent_call(
                agent_id,
                lambda: manager.team_agent_lifecycle.test_playground(
                    agent_id,
                    tuple(str(part) for part in (body.get("command") or ())),
                    environment=body.get("environment") or {},
                    resource_policy=body.get("resource_policy") or {},
                ).__dict__
            )
        if action == "stop":
            def stop_agent():
                cancellation = manager.cancel_canonical_team_scope(
                    "agent",
                    agent_id,
                    reason=str(body.get("reason") or "agent stopped by requester"),
                )
                stopped = manager.team_agent_lifecycle.stop(agent_id)
                return {**stopped, "cancellation": cancellation}

            return _team_agent_call(agent_id, stop_agent)
        actions = {
            "pause": manager.team_agent_lifecycle.pause,
            "resume": manager.team_agent_lifecycle.resume,
            "restart": manager.team_agent_lifecycle.restart,
            "archive": manager.team_agent_lifecycle.archive,
            "reset": manager.team_agent_lifecycle.reset,
        }
        if action == "delete":
            def delete_agent():
                cancellation = manager.cancel_canonical_team_scope(
                    "agent",
                    agent_id,
                    reason=str(body.get("reason") or "agent deleted by requester"),
                )
                deleted = manager.team_agent_lifecycle.delete(
                    agent_id,
                    requester=manager.team_service.requester,
                )
                return {**deleted, "cancellation": cancellation}

            return _team_agent_call(
                agent_id,
                delete_agent,
            )
        if action not in actions:
            return JSONResponse({"error": "unsupported Team agent action"}, status_code=404)
        return _team_agent_call(agent_id, lambda: actions[action](agent_id))

    @app.get("/v1/team/conversations")
    def team_conversations(include_archived: bool = False, limit: int = 50) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "conversations": manager.team_service.list_conversations(
                    include_archived=include_archived, limit=limit
                )
            }
        )

    @app.post("/v1/team/conversations")
    def team_conversation_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_conversation(
                kind=str(body.get("kind", "group")),
                title=str(body.get("title", "")),
                agent_ids=tuple(str(value) for value in (body.get("agent_ids") or ())),
                reply_mode=str(body.get("reply_mode", "mention_only")),
                coordinator_agent_id=body.get("coordinator_agent_id"),
                allow_at_all=bool(body.get("allow_at_all", False)),
                max_fanout=int(body.get("max_fanout", 8)),
            )
        )

    @app.get("/v1/team/conversations/{conversation_id}")
    def team_conversation_get(conversation_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_conversation(conversation_id))

    @app.patch("/v1/team/conversations/{conversation_id}")
    def team_conversation_update(conversation_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.update_conversation(
                conversation_id,
                title=body.get("title"),
                reply_mode=body.get("reply_mode"),
                coordinator_agent_id=body.get("coordinator_agent_id"),
                allow_at_all=body.get("allow_at_all"),
                max_fanout=body.get("max_fanout"),
            )
        )

    @app.post("/v1/team/conversations/{conversation_id}/members")
    def team_conversation_member_add(conversation_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.add_conversation_agent(
                conversation_id, str((body or {}).get("agent_id", ""))
            )
        )

    @app.delete("/v1/team/conversations/{conversation_id}/members/{agent_id}")
    def team_conversation_member_remove(conversation_id: str, agent_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.remove_conversation_agent(conversation_id, agent_id)
        )

    @app.post("/v1/team/conversations/{conversation_id}/archive")
    def team_conversation_archive(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_conversation(conversation_id, "archived")
        )

    @app.post("/v1/team/conversations/{conversation_id}/restore")
    def team_conversation_restore(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_conversation(conversation_id, "active")
        )

    @app.delete("/v1/team/conversations/{conversation_id}")
    def team_conversation_delete(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_conversation(conversation_id, "deleted")
        )

    @app.post("/v1/team/conversations/{conversation_id}/pin")
    def team_conversation_pin(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.set_conversation_pinned(conversation_id, pinned=True)
        )

    @app.delete("/v1/team/conversations/{conversation_id}/pin")
    def team_conversation_unpin(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.set_conversation_pinned(conversation_id, pinned=False)
        )

    @app.get("/v1/team/conversations/{conversation_id}/messages")
    def team_messages(
        conversation_id: str,
        cursor: Optional[str] = None,
        limit: int = 50,
        thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_conversations.list_messages(
                conversation_id,
                thread_id=thread_id,
                after_cursor=cursor,
                limit=limit,
                requester_actor_id=manager.team_actor_id,
            )
        )

    @app.post("/v1/team/conversations/{conversation_id}/messages")
    def team_message_create(conversation_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        def submit():
            result = manager.team_service.send_message(
                conversation_id,
                body=str(body.get("body", "")),
                message_id=body.get("message_id"),
                client_request_id=body.get("client_request_id"),
                mention_actor_ids=tuple(str(value) for value in (body.get("mention_actor_ids") or ())),
                at_all=bool(body.get("at_all", False)),
                fanout_confirmed=bool(body.get("fanout_confirmed", False)),
                thread_id=body.get("thread_id"),
                parent_message_id=body.get("parent_message_id"),
                quote_message_id=body.get("quote_message_id"),
                priority=int(body.get("priority", 0)),
            )
            # Canonical Team messages are durable first; nudge the worker loop
            # so a DM/group reply starts immediately instead of waiting for the
            # scheduler interval.
            manager.kick_team_tick()
            return _serialize_team_submission(result)
        return _team_call(submit)

    @app.post("/v1/team/messages/{message_id}/edit")
    def team_message_edit(message_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.edit_message(
                message_id,
                str((body or {}).get("body", "")),
                expected_revision=(body or {}).get("expected_revision"),
            )
        )

    @app.delete("/v1/team/messages/{message_id}")
    def team_message_delete(message_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.delete_message(message_id)
        )

    @app.post("/v1/team/messages/{message_id}/attachments")
    def team_message_attachment_add(message_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        # Authorize before writing bytes so a caller cannot create an orphaned blob
        # by uploading against a message they cannot see.
        try:
            manager.team_service.get_message(message_id)
        except TeamContractError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        encoded = str(body.get("data_b64", ""))
        if len(encoded) > 15_000_000:
            return JSONResponse({"error": "attachment exceeds 10MB"}, status_code=400)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse({"error": "data_b64 is not valid base64"}, status_code=400)
        filename = str(body.get("filename", ""))
        try:
            storage_ref, media_type = manager.attachment_store.put_message(data, filename)
        except TeamsBoardError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        sha256 = hashlib.sha256(data).hexdigest()
        attachment_id = "attachment_" + hashlib.sha256(
            f"{message_id}:{storage_ref}".encode("utf-8")
        ).hexdigest()[:32]
        return _team_call(
            lambda: manager.team_service.add_message_attachment(
                message_id,
                {
                    "attachment_id": attachment_id,
                    "storage_ref": storage_ref,
                    "filename": filename,
                    "media_type": media_type,
                    "size_bytes": len(data),
                    "sha256": sha256,
                    "metadata": {"source": "team_message"},
                },
            )
        )

    @app.get("/v1/team/messages/{message_id}/attachments/{attachment_id}")
    def team_message_attachment_get(message_id: str, attachment_id: str):
        def read_attachment():
            attachment = manager.team_service.get_message_attachment(message_id, attachment_id)
            stored = team_attachment_stored_name(str(attachment.get("storage_ref", "")))
            if stored is None:
                raise TeamContractError("attachment storage reference is invalid")
            try:
                path = manager.attachment_store.path_for(stored)
            except TeamsBoardError as error:
                raise TeamContractError("attachment blob is unavailable") from error
            return Response(
                content=path.read_bytes(),
                media_type=str(attachment.get("media_type") or "application/octet-stream"),
            )

        return _team_call(read_attachment)

    @app.post("/v1/team/messages/{message_id}/translate")
    def team_message_translate(message_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.translate_team_message(
                message_id,
                str((body or {}).get("language", "")),
            )
        )

    @app.post("/v1/team/messages/{message_id}/reaction")
    def team_message_reaction(message_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.set_message_reaction(
                message_id,
                str(body.get("emoji", "")),
                active=bool(body.get("active", True)),
            )
        )

    @app.get("/v1/team/conversations/{conversation_id}/pins")
    def team_pinned_messages(conversation_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "pins": manager.team_service.list_pinned_messages(conversation_id)
            }
        )

    @app.post("/v1/team/conversations/{conversation_id}/messages/{message_id}/pin")
    def team_message_pin(conversation_id: str, message_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.pin_message(conversation_id, message_id)
        )

    @app.delete("/v1/team/conversations/{conversation_id}/messages/{message_id}/pin")
    def team_message_unpin(conversation_id: str, message_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.unpin_message(conversation_id, message_id)
        )

    @app.post("/v1/team/conversations/{conversation_id}/threads")
    def team_thread_open(conversation_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.open_thread(
                conversation_id, str((body or {}).get("root_message_id", ""))
            )
        )

    @app.get("/v1/team/threads/{thread_id}")
    def team_thread_get(thread_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_thread(thread_id))

    @app.post("/v1/team/conversations/{conversation_id}/read")
    def team_conversation_read(conversation_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.set_read_cursor(
                conversation_id,
                (body or {}).get("message_id"),
            )
        )

    @app.get("/v1/team/crews")
    def team_crew_list(
        status: Optional[list[str]] = Query(None),
        limit: int = 50,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "crew_runs": manager.team_service.list_crew_runs(
                    statuses=tuple(status or ()), limit=limit
                )
            }
        )

    @app.post("/v1/team/crews")
    def team_crew_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_crew_run(
                agent_ids=tuple(str(value) for value in (body.get("agent_ids") or ())),
                root_task_id=body.get("root_task_id"),
                agent_roles={
                    str(key): str(value)
                    for key, value in (body.get("agent_roles") or {}).items()
                },
                client_request_id=body.get("client_request_id"),
            )
        )

    @app.get("/v1/team/crews/{crew_run_id}")
    def team_crew_get(crew_run_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_crew_run(crew_run_id))

    @app.post("/v1/team/crews/{crew_run_id}/transition")
    def team_crew_transition(crew_run_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_crew_run(
                crew_run_id, str((body or {}).get("status", ""))
            )
        )

    @app.get("/v1/team/integrations")
    def team_integration_list(
        task_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "integrations": manager.team_service.list_integrations(
                    task_id=task_id, limit=limit
                )
            }
        )

    @app.post("/v1/team/integrations")
    def team_integration_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_integration(
                task_id=str(body.get("task_id", "")),
                source_ref=str(body.get("source_ref", "")),
                target_ref=str(body.get("target_ref", "")),
                run_id=body.get("run_id"),
                artifact_id=body.get("artifact_id"),
                source_agent_id=body.get("source_agent_id"),
                idempotency_key=body.get("idempotency_key"),
            )
        )

    @app.get("/v1/team/integrations/{integration_id}")
    def team_integration_get(integration_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_integration(integration_id))

    @app.post("/v1/team/integrations/{integration_id}/transition")
    def team_integration_transition(integration_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.transition_integration(
                integration_id,
                str(body.get("status", "")),
                conflict_detail=str(body.get("conflict_detail", "")),
            )
        )

    @app.get("/v1/team/connectors/identities")
    def team_connector_identities(
        connector_kind: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "identities": manager.team_service.list_connector_identities(
                    connector_kind=connector_kind,
                    limit=limit,
                )
            }
        )

    @app.post("/v1/team/connectors/identities")
    def team_connector_identity_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.register_connector_identity(
                connector_kind=str(body.get("connector_kind", "")),
                external_workspace_id=str(body.get("external_workspace_id", "")),
                external_identity_id=str(body.get("external_identity_id", "")),
                account_ref=str(body.get("account_ref", "")),
                display_name=str(body.get("display_name", "")),
                metadata=body.get("metadata") or {},
                record_id=body.get("record_id"),
            )
        )

    @app.get("/v1/team/connectors/identities/{record_id}")
    def team_connector_identity_get(record_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_connector_identity(record_id))

    @app.post("/v1/team/connectors/identities/{record_id}/transition")
    def team_connector_identity_transition(record_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_connector_identity(
                record_id,
                str((body or {}).get("status", "")),
            )
        )

    @app.post("/v1/team/connectors/identities/{record_id}/health")
    def team_connector_identity_health(record_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.update_connector_identity_health(
                record_id,
                str(body.get("health_status", "")),
                error_detail=body.get("error_detail"),
                reconnect=bool(body.get("reconnect", False)),
            )
        )

    @app.get("/v1/team/connectors/grants")
    def team_connector_grants(
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "grants": manager.team_service.list_connector_grants(
                    agent_id=agent_id,
                    limit=limit,
                )
            }
        )

    @app.post("/v1/team/connectors/grants")
    def team_connector_grant_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.grant_connector_agent(
                external_identity_record_id=str(body.get("external_identity_record_id", "")),
                agent_id=str(body.get("agent_id", "")),
                actions=body.get("actions") or [],
                resource_ids=body.get("resource_ids") or [],
                requester_actor_id=body.get("requester_actor_id"),
                expires_at=body.get("expires_at"),
                grant_id=body.get("grant_id"),
                binding_id=body.get("binding_id"),
                destination_conversation_id=body.get("destination_conversation_id"),
                destination_thread_id=body.get("destination_thread_id"),
            )
        )

    @app.post("/v1/team/connectors/grants/{grant_id}/revoke")
    def team_connector_grant_revoke(grant_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.revoke_connector_grant(grant_id))

    @app.get("/v1/team/connectors/bindings")
    def team_connector_bindings(
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "bindings": manager.team_service.list_connector_bindings(
                    agent_id=agent_id,
                    limit=limit,
                )
            }
        )

    @app.post("/v1/team/connectors/bindings/{binding_id}/revoke")
    def team_connector_binding_revoke(binding_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.revoke_connector_binding(binding_id))

    @app.post("/v1/team/connectors/events")
    def team_connector_event_ingest(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: _serialize_connector_submission(
                manager.team_service.ingest_connector_event(
                    external_identity_record_id=str(
                        body.get("external_identity_record_id", "")
                    ),
                    external_event_id=str(body.get("external_event_id", "")),
                    external_resource_id=str(body.get("external_resource_id", "")),
                    target_agent_id=str(body.get("target_agent_id", "")),
                    instruction=str(body.get("instruction", "")),
                    payload=body.get("payload") or {},
                    event_type=str(body.get("event_type", "message")),
                    external_thread_id=str(body.get("external_thread_id", "")),
                    destination_conversation_id=body.get("destination_conversation_id"),
                    destination_thread_id=body.get("destination_thread_id"),
                    requester_actor_id=body.get("requester_actor_id"),
                    idempotency_key=body.get("idempotency_key"),
                    budget=WorkBudget.from_dict(body.get("budget")),
                    priority=body.get("priority", 0),
                )
            )
        )

    @app.get("/v1/team/connectors/events")
    def team_connector_events(
        status: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "events": manager.team_service.list_connector_events(
                    status=status,
                    limit=limit,
                )
            }
        )

    @app.get("/v1/team/connectors/outbox")
    def team_connector_outbox(
        status: Optional[list[str]] = Query(None),
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "outbox": manager.team_service.list_connector_outbox(
                    statuses=status or (),
                    limit=limit,
                )
            }
        )

    @app.post("/v1/team/connectors/outbox/{delivery_id}/send")
    def team_connector_outbox_send(delivery_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.send_connector_outbox(delivery_id))

    @app.get("/v1/team/tasks")
    def team_tasks(
        status: Optional[list[str]] = Query(None),
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.list_tasks(
                statuses=tuple(status or ()), cursor=cursor, limit=limit
            )
        )

    @app.post("/v1/team/tasks")
    def team_task_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_task(
                title=str(body.get("title", "")),
                description=str(body.get("description", "")),
                acceptance_criteria=str(body.get("acceptance_criteria", "")),
                owner_agent_id=body.get("owner_agent_id"),
                reviewer_actor_id=body.get("reviewer_actor_id"),
                canonical_thread_id=body.get("canonical_thread_id"),
                parent_task_id=body.get("parent_task_id"),
                priority=int(body.get("priority", 0)),
                due_at=body.get("due_at"),
            )
        )

    @app.get("/v1/team/tasks/{task_id}/thread")
    def team_task_thread(task_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_task_thread(task_id))

    @app.post("/v1/team/tasks/{task_id}/summary")
    def team_task_summary(task_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.post_task_summary(
                task_id,
                conversation_id=str(body.get("conversation_id", "")),
                summary=str(body.get("summary", "")),
                client_request_id=body.get("client_request_id"),
            )
        )

    @app.get("/v1/team/tasks/{task_id}")
    def team_task_get(task_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_task(task_id))

    @app.get("/v1/team/artifacts")
    def team_artifact_list(limit: int = 100) -> dict[str, Any]:
        return _team_call(
            lambda: {"artifacts": manager.team_service.list_artifacts(limit=limit)}
        )

    @app.post("/v1/team/artifacts/publish")
    def team_artifact_publish(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.publish_artifact(
                source_run_id=str(body.get("source_run_id", "")),
                source_task_id=body.get("source_task_id"),
                filename=str(body.get("filename", "")),
                media_type=str(body.get("media_type", "")),
                size_bytes=int(body.get("size_bytes", -1)),
                sha256=str(body.get("sha256", "")),
                visibility=str(body.get("visibility", "")),
                scope_id=str(body.get("scope_id", "")),
                storage_ref=str(body.get("storage_ref", "")),
                artifact_id=body.get("artifact_id"),
                revision_id=body.get("revision_id"),
                idempotency_key=body.get("idempotency_key"),
                metadata=body.get("metadata") or {},
            )
        )

    @app.post("/v1/team/artifacts/{artifact_id}/transition")
    def team_artifact_transition(artifact_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_artifact(
                artifact_id, str((body or {}).get("status", ""))
            )
        )

    @app.get("/v1/team/artifacts/{artifact_id}/download")
    def team_artifact_download(artifact_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.artifact_download(artifact_id))

    @app.get("/v1/team/artifacts/{artifact_id}/content")
    def team_artifact_content(artifact_id: str) -> Response:
        """Serve only locally materialized artifact blobs after the Team ACL check."""

        def read_content() -> Response:
            descriptor = manager.team_service.artifact_download(artifact_id)
            stored = team_attachment_stored_name(str(descriptor.get("storage_ref", "")))
            if stored is None:
                return JSONResponse(
                    {"error": "artifact content is managed by an external storage provider"},
                    status_code=409,
                )
            try:
                path = manager.attachment_store.path_for(stored)
                data = path.read_bytes()
            except (TeamsBoardError, OSError):
                return JSONResponse({"error": "artifact blob is unavailable"}, status_code=404)
            if len(data) != int(descriptor["size_bytes"]) or hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
                return JSONResponse({"error": "artifact integrity check failed"}, status_code=409)
            filename = str(descriptor["filename"]).replace('"', "")
            return Response(
                content=data,
                media_type=str(descriptor["media_type"] or "application/octet-stream"),
                headers={
                    "Cache-Control": "no-store",
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

        return _team_call(read_content)

    @app.get("/v1/team/artifacts/{artifact_id}/preview")
    def team_artifact_preview(artifact_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.artifact_preview(artifact_id))

    @app.post("/v1/team/artifacts/{artifact_id}/quarantine")
    def team_artifact_quarantine(artifact_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.quarantine_artifact(
                artifact_id, str((body or {}).get("reason", ""))
            )
        )

    @app.post("/v1/team/artifacts/{artifact_id}/quarantine/release")
    def team_artifact_release_quarantine(artifact_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.release_artifact_quarantine(artifact_id)
        )

    @app.post("/v1/team/artifacts/{artifact_id}/links")
    def team_artifact_link(artifact_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.link_artifact(
                artifact_id,
                ResourceRef.from_dict((body or {}).get("aggregate") or {}),
            )
        )

    @app.get("/v1/team/artifacts/{artifact_id}/compare")
    def team_artifact_compare(
        artifact_id: str, left_revision_id: str, right_revision_id: str
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.compare_artifact_revisions(
                artifact_id, left_revision_id, right_revision_id
            )
        )

    @app.get("/v1/team/artifacts/{artifact_id}")
    def team_artifact_get(artifact_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_artifact(artifact_id))

    @app.get("/v1/team/conversations/{conversation_id}/context")
    def team_context_get(
        conversation_id: str,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        max_messages: int = 50,
        max_chars: int = 32_000,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.build_context(
                conversation_id,
                agent_id=agent_id,
                task_id=task_id,
                max_messages=max_messages,
                max_chars=max_chars,
            )
        )

    @app.post("/v1/team/context/memories")
    def team_memory_create(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_memory(
                scope=str(body.get("scope", "")),
                scope_id=str(body.get("scope_id", "")),
                content=str(body.get("content", "")),
                expires_at=body.get("expires_at"),
                source_artifact_id=body.get("source_artifact_id"),
                source_run_id=body.get("source_run_id"),
            )
        )

    @app.get("/v1/team/context/memories")
    def team_memory_list(
        scope: str,
        scope_id: str,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "memories": manager.team_service.list_memories(
                    scope=scope, scope_id=scope_id, include_expired=include_expired
                )
            }
        )

    @app.put("/v1/team/context/memories/{memory_id}")
    def team_memory_update(memory_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.update_memory(
                memory_id,
                content=str(body.get("content", "")),
                expires_at=body.get("expires_at"),
            )
        )

    @app.delete("/v1/team/context/memories/{memory_id}")
    def team_memory_delete(memory_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.delete_memory(memory_id))

    @app.get("/v1/team/context/memory-candidates", response_model=None)
    def team_memory_candidates(
        scope: Optional[str] = None,
        scope_id: Optional[str] = None,
        status: Optional[str] = "pending",
        limit: int = 200,
    ) -> dict[str, Any] | JSONResponse:
        def list_candidates():
            manager.team_service._require_workspace_member()
            if (scope is None) != (scope_id is None):
                raise TeamContractError("scope and scope_id must be provided together")
            if scope is not None and scope_id is not None:
                candidates = manager.team_memory.candidates.list_scope(
                    manager.team_workspace_id,
                    scope,
                    scope_id,
                    status=None if status in {None, "", "all"} else status,
                )
                candidates = candidates[:limit]
            else:
                candidates = manager.team_memory.candidates.list_for_workspace(
                    manager.team_workspace_id,
                    status=None if status in {None, "", "all"} else status,
                    limit=limit,
                )
            return {"candidates": candidates}

        return _team_call(list_candidates)

    @app.post("/v1/team/context/memory-candidates", response_model=None)
    def team_memory_candidate_propose(body: dict) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def propose():
            manager.team_service._require_workspace_action("run.execute")
            items = body.get("candidates") or []
            if isinstance(items, (str, bytes)) or not isinstance(items, list):
                raise TeamContractError("candidates must be a list")
            run_id = str(body.get("run_id") or "").strip()
            scope = str(body.get("scope") or "").strip()
            scope_id = str(body.get("scope_id") or "").strip()
            # This endpoint is an explicit human/system proposal surface.  It
            # never treats arbitrary model text as memory without a caller
            # supplying bounded candidate records.
            return {
                "candidates": manager.team_memory.propose_from_run(
                    run_id,
                    scope=scope,
                    scope_id=scope_id,
                    proposed_by_actor_id=manager.team_actor_id,
                    extractor=lambda _source: items[:64],
                )
            }

        return _team_call(propose)

    @app.get("/v1/team/context/memory-candidates/{candidate_id}", response_model=None)
    def team_memory_candidate_get(candidate_id: str) -> dict[str, Any] | JSONResponse:
        def get_candidate():
            manager.team_service._require_workspace_member()
            candidate = manager.team_memory.candidates.get(candidate_id)
            if candidate is None or candidate["workspace_id"] != manager.team_workspace_id:
                raise TeamContractError("memory candidate not found")
            return candidate

        return _team_call(get_candidate)

    @app.post("/v1/team/context/memory-candidates/{candidate_id}/decide", response_model=None)
    def team_memory_candidate_decide(
        candidate_id: str, body: dict
    ) -> dict[str, Any] | JSONResponse:
        body = body or {}

        def decide():
            manager.team_service._require_workspace_action("approval.decide")
            status = str(body.get("status") or "").strip().lower()
            if status == "accepted":
                return manager.team_memory.accept(
                    candidate_id, actor_id=manager.team_actor_id
                )
            if status == "rejected":
                return manager.team_memory.reject(
                    candidate_id, actor_id=manager.team_actor_id
                )
            raise TeamContractError("memory candidate decision must be accepted or rejected")

        return _team_call(decide)

    @app.post("/v1/team/context/pins")
    def team_context_pin(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.pin_context(
                scope=str(body.get("scope", "")),
                scope_id=str(body.get("scope_id", "")),
                resource=ResourceRef.from_dict(body.get("resource") or {}),
                expires_at=body.get("expires_at"),
            )
        )

    @app.post("/v1/team/context/shares")
    def team_context_share(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.create_context_share(
                resource=ResourceRef.from_dict(body.get("resource") or {}),
                source=ContextBoundary.from_dict(body.get("source") or {}),
                target=ContextBoundary.from_dict(body.get("target") or {}),
                reason=str(body.get("reason", "")),
                idempotency_key=body.get("idempotency_key"),
            )
        )

    @app.delete("/v1/team/context/pins")
    def team_context_unpin(body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: {
                "removed": manager.team_service.unpin_context(
                    scope=str(body.get("scope", "")),
                    scope_id=str(body.get("scope_id", "")),
                    resource=ResourceRef.from_dict(body.get("resource") or {}),
                )
            }
        )

    @app.post("/v1/team/tasks/{task_id}/dependencies")
    def team_task_dependency(task_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.add_task_dependency(
                task_id, str((body or {}).get("depends_on_task_id", ""))
            )
        )

    @app.post("/v1/team/tasks/{task_id}/transition")
    def team_task_transition(task_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_task(
                task_id, str((body or {}).get("status", ""))
            )
        )

    @app.post("/v1/team/tasks/{task_id}/review")
    def team_task_review(task_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: manager.team_service.review_task(
                task_id,
                approved=bool(body.get("approved", False)),
                comment=str(body.get("comment", "")),
            )
        )

    @app.post("/v1/team/tasks/{task_id}/dispatch")
    def team_task_dispatch(task_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: _serialize_team_submission(
                manager.team_service.dispatch_task(
                    task_id,
                    instruction=body.get("instruction"),
                    expected_output=body.get("expected_output"),
                    budget=body.get("budget") or {},
                    priority=(int(body["priority"]) if "priority" in body else None),
                    client_request_id=body.get("client_request_id"),
                    crew_run_id=body.get("crew_run_id"),
                )
            )
        )

    @app.post("/v1/team/tasks/{task_id}/assign")
    def team_task_assign(task_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.assign_task(
                task_id, str((body or {}).get("owner_agent_id", ""))
            )
        )

    @app.post("/v1/team/tasks/{task_id}/watchers")
    def team_task_watcher(task_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.add_task_watcher(
                task_id, (body or {}).get("actor_id")
            )
        )

    @app.delete("/v1/team/tasks/{task_id}/watchers/{actor_id}")
    def team_task_watcher_remove(task_id: str, actor_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.remove_task_watcher(task_id, actor_id)
        )

    @app.post("/v1/team/tasks/{task_id}/labels")
    def team_task_label(task_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.add_task_label(
                task_id, str((body or {}).get("label", ""))
            )
        )

    @app.delete("/v1/team/tasks/{task_id}/labels/{label}")
    def team_task_label_remove(task_id: str, label: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.remove_task_label(task_id, label)
        )

    @app.get("/v1/team/tasks/{task_id}/events")
    def team_task_events(task_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: {"events": manager.team_service.list_task_events(task_id)}
        )

    @app.get("/v1/team/tasks/{task_id}/verifications")
    def team_task_verifications(task_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: {"verifications": manager.team_service.list_task_verifications(task_id)}
        )

    @app.post("/v1/team/tasks/{task_id}/cancel")
    def team_task_cancel(task_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_task(task_id, "canceled")
        )

    @app.post("/v1/team/tasks/{task_id}/reopen")
    def team_task_reopen(task_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.transition_task(task_id, "open")
        )

    @app.get("/v1/team/tasks/{task_id}/handoffs")
    def team_task_handoffs(task_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: {"handoffs": manager.team_service.list_handoffs(task_id)}
        )

    @app.post("/v1/team/tasks/{task_id}/handoffs")
    def team_task_handoff(task_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return _team_call(
            lambda: _serialize_team_submission(
                manager.team_service.create_handoff(
                    task_id,
                    sender_agent_id=str(body.get("sender_agent_id", "")),
                    recipient_agent_id=str(body.get("recipient_agent_id", "")),
                    purpose=str(body.get("purpose", "")),
                    expected_output=str(body.get("expected_output", "")),
                    artifact_ids=tuple(str(value) for value in (body.get("artifact_ids") or ())),
                    budget=body.get("budget") or {},
                    depth=int(body.get("depth", 1)),
                    agent_path=tuple(str(value) for value in (body.get("agent_path") or ())),
                    idempotency_key=body.get("idempotency_key"),
                    correlation_id=body.get("correlation_id"),
                )
            )
        )

    @app.get("/v1/team/search")
    def team_search(
        q: str = "",
        kind: Optional[list[str]] = Query(None),
        cursor: Optional[str] = None,
        limit: int = 50,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.search_all(
                q,
                kinds=tuple(kind or ()),
                cursor=cursor,
                limit=limit,
                semantic=semantic,
            )
        )

    @app.get("/v1/team/notifications")
    def team_notifications(
        unread_only: bool = False,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.list_notifications(
                unread_only=unread_only, cursor=cursor, limit=limit
            )
        )

    @app.post("/v1/team/notifications/{notification_id}/read")
    def team_notification_read(notification_id: str) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_governance.mark_notification_read(
                notification_id, manager.team_actor_id
            )
        )

    @app.get("/v1/team/notification-preferences")
    def team_notification_preferences() -> dict[str, Any]:
        return _team_call(manager.team_service.get_notification_preferences)

    @app.patch("/v1/team/notification-preferences")
    def team_notification_preferences_update(body: dict) -> dict[str, Any]:
        body = body or {}
        allowed = {"muted", "mention_only", "quiet_hours", "event_preferences"}
        changes = {key: body[key] for key in allowed if key in body}
        return _team_call(
            lambda: manager.team_service.update_notification_preferences(**changes)
        )

    @app.get("/v1/team/approvals")
    def team_approvals(
        status: Optional[list[str]] = Query(None),
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "approvals": manager.team_service.list_approvals(
                    statuses=tuple(status or ()),
                    request_id=request_id,
                    run_id=run_id,
                    limit=limit,
                )
            }
        )

    @app.get("/v1/team/approvals/{approval_id}")
    def team_approval_get(approval_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_approval(approval_id))

    @app.post("/v1/team/approvals/{approval_id}/decide")
    def team_approval_decide(approval_id: str, body: dict) -> dict[str, Any]:
        def decide():
            result = manager.team_service.decide_approval(
                approval_id, str((body or {}).get("status", ""))
            )
            manager.kick_team_tick()
            return result

        return _team_call(decide)

    @app.post("/v1/team/approvals/{approval_id}/cancel")
    def team_approval_cancel(approval_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.cancel_approval(approval_id))

    @app.get("/v1/team/events")
    def team_events(after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.list_events(
                after_sequence=after_sequence, limit=limit
            )
        )

    @app.get("/v1/team/activity")
    def team_activity(after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        return _team_call(
            lambda: manager.team_service.list_activity(
                after_sequence=after_sequence, limit=limit
            )
        )

    @app.get("/v1/team/runs")
    def team_runs(
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[list[str]] = Query(None),
        limit: int = 50,
    ) -> dict[str, Any]:
        return _team_call(
            lambda: {
                "runs": manager.team_service.list_runs(
                    agent_id=agent_id,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    statuses=tuple(status or ()),
                    limit=limit,
                )
            }
        )

    @app.get("/v1/team/runs/{run_id}")
    def team_run_get(run_id: str) -> dict[str, Any]:
        return _team_call(lambda: manager.team_service.get_run(run_id))

    @app.post("/v1/team/runs/{run_id}/cancel")
    def team_run_cancel(run_id: str, body: dict) -> dict[str, Any]:
        return _team_call(
            lambda: manager.cancel_canonical_team_run(
                run_id,
                reason=str((body or {}).get("reason") or "canceled by requester"),
            )
        )

    @app.post("/v1/team/scopes/{scope_kind}/{scope_id}/cancel")
    def team_scope_cancel(scope_kind: str, scope_id: str, body: dict) -> dict[str, Any]:
        """Stop all queued/live work in a conversation, task tree, or crew run."""

        return _team_call(
            lambda: manager.cancel_canonical_team_scope(
                scope_kind,
                scope_id,
                reason=str((body or {}).get("reason") or "canceled by requester"),
            )
        )

    # ---- Workspace Backup & Restore Subsystem
    @app.get("/v1/team/workspace/export")
    def team_workspace_export(
        include_attachments: bool = True,
        include_transcripts: bool = True,
    ) -> Response:
        try:
            manager.team_service._require_workspace_member()
            archive_bytes = manager.team_backup.export_workspace(
                manager.team_workspace_id,
                include_attachments=include_attachments,
                include_transcripts=include_transcripts,
            )
            filename = f"workspace_{manager.team_workspace_id}_{int(datetime.now(timezone.utc).timestamp())}.owb"
            return Response(
                content=archive_bytes,
                media_type="application/gzip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.post("/v1/team/workspace/import")
    async def team_workspace_import(
        request: Request,
        clone: bool = False,
        target_workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        try:
            body = await request.body()
            if not body:
                return JSONResponse({"error": "request body is empty"}, status_code=400)
            result = manager.team_backup.restore_workspace(
                body,
                target_workspace_id=target_workspace_id,
                clone_as_new=clone,
            )
            return result
        except Exception as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.post("/v1/team/database/backup")
    def team_database_backup() -> dict[str, Any]:
        return _team_call(
            lambda: {"ok": True, "path": manager.team_database.create_backup()}
        )

    @app.get("/v1/team/cutover", response_model=None)
    def team_cutover_preflight() -> dict[str, Any] | JSONResponse:
        """Read-only evidence for importing the legacy local Team stores."""

        return _team_call(
            lambda: manager.team_cutover.preflight(manager._data_base).to_dict()
        )

    @app.post("/v1/team/cutover", response_model=None)
    def team_cutover(body: dict) -> dict[str, Any] | JSONResponse:
        """Run an explicit legacy import or verify its durable mapping ledger.

        Import is intentionally confirmation-gated.  The endpoint never deletes,
        rewrites, or executes anything from the legacy stores.
        """

        body = body or {}

        def run():
            manager.team_service._require_workspace_action("agent.manage")
            action = str(body.get("action") or "verify").strip().lower()
            if action == "verify":
                return manager.team_cutover.verify(manager._data_base).to_dict()
            if action == "import":
                if body.get("confirm") is not True:
                    raise TeamContractError("legacy import requires confirm=true")
                return manager.team_cutover.import_legacy(
                    manager._data_base,
                    create_backup=body.get("create_backup", True) is not False,
                    activate=body.get("activate") is True,
                ).to_dict()
            if action == "preflight":
                return manager.team_cutover.preflight(manager._data_base).to_dict()
            raise TeamContractError("cutover action must be preflight, import, or verify")

        return _team_call(run)

    # ---- The open board surface (OPE-100): token-authenticated `/v1/board` API.
    # Identity is the TOKEN (actor+role bound at mint, resolved per request, never
    # client-asserted); authority is the STORE — the same double gate in-app agents
    # get. This is the one wire protocol every external front door rides:
    # RemoteDialect (the `ocw` CLI, the team-board MCP server, headless instances)
    # today, a hosted board service later. Tokens are required even on loopback —
    # they carry identity, not just access.

    def _board_actor(request: Request):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        return manager.board_tokens.resolve(token)

    def _board(request: Request, handler):
        actor = _board_actor(request)
        if actor is None:
            return JSONResponse(
                {"error": "board token required (Authorization: Bearer …) — mint"
                          " one with `ocw board token` on the serving machine"},
                status_code=401,
            )
        try:
            return handler(actor)
        except TeamsBoardNotFoundError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
        except TeamsAuthorityError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except (TeamsBoardError, ValueError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    def _canonical_board_active() -> bool:
        """Return the durable cutover switch without making legacy stores authoritative."""

        return manager.team_cutover.is_active()

    def _canonical_auxiliary_route() -> JSONResponse | None:
        """Fence board features without a canonical projection after cutover.

        Keeping these routes pointed at the old store after activation would
        silently reintroduce a second source of truth.  The task/item surface is
        adapted above; artifact, feed, policy, and journal callers must use their
        canonical Team endpoints until their dedicated projections exist.
        """

        if _canonical_board_active():
            return JSONResponse(
                {
                    "error": "legacy board route is fenced after cutover; use the canonical Team API"
                },
                status_code=409,
            )
        return None

    def _board_auxiliary(request: Request, handler):
        """Authenticate the board token before exposing the cutover fence."""

        def guarded(actor):
            fenced = _canonical_auxiliary_route()
            if fenced is not None:
                return fenced
            return handler(actor)

        return _board(request, guarded)

    @app.get("/v1/board/whoami")
    def board_whoami(request: Request):
        return _board(
            request, lambda actor: {"actor": actor.id, "role": actor.role.value}
        )

    @app.get("/v1/board/spaces")
    def board_spaces(request: Request):
        return _board(
            request,
            lambda actor: {
                "spaces": (
                    manager.team_board_adapter.spaces()
                    if _canonical_board_active()
                    else manager.team_store.spaces()
                )
            },
        )

    @app.get("/v1/board/items")
    def board_list_items(
        request: Request, space: str, state: str = "", assignee: str = ""
    ):
        return _board(
            request,
            lambda actor: {
                "items": (
                    manager.team_board_adapter.list_items(
                        space, actor, state=state or None, assignee=assignee or None
                    )
                    if _canonical_board_active()
                    else manager.team_store.list_items(
                        space, actor, state=state or None, assignee=assignee or None
                    )
                )
            },
        )

    @app.get("/v1/board/item")
    def board_get_item(request: Request, space: str, id: int):
        return _board(
            request,
            lambda actor: (
                manager.team_board_adapter.get_item(space, int(id), actor=actor)
                if _canonical_board_active()
                else manager.team_store.get_item(space, int(id), actor=actor)
            ),
        )

    @app.post("/v1/board/items")
    def board_create_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            if _canonical_board_active():
                item = manager.team_board_adapter.create_item(
                    str(body.get("space", "")),
                    actor,
                    title=str(body.get("title", "")),
                    criteria=str(body.get("criteria", "")),
                    description=str(body.get("description", "")),
                    parent=(
                        int(body["parent"]) if body.get("parent") is not None else None
                    ),
                    case=str(body.get("case") or "") or None,
                    request_id=str(body.get("request_id") or "") or None,
                )
            else:
                item = manager.team_store.create_item(
                    str(body.get("space", "")),
                    actor,
                    title=str(body.get("title", "")),
                    criteria=str(body.get("criteria", "")),
                    description=str(body.get("description", "")),
                    parent=(
                        int(body["parent"]) if body.get("parent") is not None else None
                    ),
                    case=str(body.get("case") or "") or None,
                )
            manager.kick_team_tick()  # a new filing is lead-subscription news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/transition")
    def board_transition_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            if _canonical_board_active():
                item = manager.team_board_adapter.transition(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("to", "")),
                    comment=str(body.get("comment", "")),
                    refs=[str(ref) for ref in body.get("refs") or []],
                    request_id=str(body.get("request_id") or "") or None,
                )
            else:
                item = manager.team_store.transition(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("to", "")),
                    comment=str(body.get("comment", "")),
                    refs=[str(ref) for ref in body.get("refs") or []],
                )
            manager.kick_team_tick()  # review/blocked should reach the lead now
            return item

        return _board(request, run)

    @app.post("/v1/board/items/comment")
    def board_comment_item(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: (
                manager.team_board_adapter.comment(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("body", "")),
                    refs=[str(ref) for ref in body.get("refs") or []],
                    request_id=str(body.get("request_id") or "") or None,
                )
                if _canonical_board_active()
                else manager.team_store.comment(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("body", "")),
                    refs=[str(ref) for ref in body.get("refs") or []],
                )
            ),
        )

    @app.post("/v1/board/items/assign")
    def board_assign_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            if _canonical_board_active():
                item = manager.team_board_adapter.assign(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("assignee", "")),
                )
            else:
                item = manager.team_store.assign(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("id", 0)),
                    str(body.get("assignee", "")),
                )
            manager.kick_team_tick()  # the assignee's queue has news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/claim")
    def board_claim_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = (
                manager.team_board_adapter.claim(
                    str(body.get("space", "")), actor, int(body.get("id", 0))
                )
                if _canonical_board_active()
                else manager.team_store.claim(
                    str(body.get("space", "")), actor, int(body.get("id", 0))
                )
            )
            manager.kick_team_tick()  # claims land in the lead's feed
            return item

        return _board(request, run)

    @app.post("/v1/board/link")
    def board_link_items(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: (
                manager.team_board_adapter.link(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("src", 0)),
                    str(body.get("kind", "")),
                    int(body.get("dst", 0)),
                )
                if _canonical_board_active()
                else manager.team_store.link(
                    str(body.get("space", "")),
                    actor,
                    int(body.get("src", 0)),
                    str(body.get("kind", "")),
                    int(body.get("dst", 0)),
                )
            ),
        )

    @app.post("/v1/board/items/attach")
    def board_attach(request: Request, body: dict):
        body = body or {}

        def run(actor):
            raw = str(body.get("data_b64", ""))
            # Cheap pre-decode bound: base64 is ~4/3 of the payload, so anything
            # multiples over the cap is refused before allocating the decode.
            if len(raw) > 15 * 1024 * 1024:
                return JSONResponse(
                    {"error": "attachment exceeds 10MB"}, status_code=400
                )
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return JSONResponse(
                    {"error": "data_b64 is not valid base64"}, status_code=400
                )
            ref = manager.attachment_store.put(
                data, str(body.get("filename", ""))
            )
            filename = str(body.get("filename", ""))
            event = manager.team_store.attach_ref(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("caption", "")) or f"attached {filename}",
                ref,
            )
            return {"ref": ref, "seq": event["seq"]}

        return _board_auxiliary(request, run)

    @app.get("/v1/board/attachment")
    def board_attachment(request: Request, name: str, space: str):
        def run(actor):
            from fastapi.responses import Response

            manager.team_store.require_attachment_access(space, actor, name)
            path = manager.attachment_store.path_for(name)
            return Response(
                content=path.read_bytes(),
                media_type=manager.attachment_store.mime_for(name),
            )

        return _board_auxiliary(request, run)

    @app.get("/v1/board/policy")
    def board_get_policy(request: Request, space: str):
        return _board_auxiliary(request, lambda actor: manager.team_store.policy(space))

    @app.post("/v1/board/policy")
    def board_set_policy(request: Request, body: dict):
        body = body or {}
        return _board_auxiliary(
            request,
            lambda actor: manager.team_store.set_policy(
                str(body.get("space", "")), actor, claims=str(body.get("claims", ""))
            ),
        )

    @app.get("/v1/board/pending")
    def board_pending(request: Request, space: str, limit: int = 200):
        # The actor's FEED: events on its slice since its cursor — interest
        # follows the assignment relation, same projection in-app workers use.
        return _board_auxiliary(
            request,
            lambda actor: {
                "events": manager.team_store.feed_for(
                    space, actor.id, limit=int(limit)
                )
            },
        )

    @app.post("/v1/board/consume")
    def board_consume(request: Request, body: dict):
        body = body or {}

        def run(actor):
            manager.team_store.consume_feed(
                str(body.get("space", "")), actor.id, int(body.get("upto_seq", 0))
            )
            return {"ok": True}

        return _board_auxiliary(request, run)

    @app.get("/v1/board/journal/cases")
    def board_journal_cases(request: Request):
        return _board_auxiliary(
            request, lambda actor: {"cases": manager.journal_store.overview(actor)}
        )

    @app.get("/v1/board/journal")
    def board_journal_read(
        request: Request,
        case: str,
        item: Optional[int] = None,
        author: str = "",
        kind: str = "",
        entity: str = "",
        include_raw: str = "",
        limit: int = 100,
    ):
        return _board_auxiliary(
            request,
            lambda actor: {
                "entries": manager.journal_store.read(
                    actor,
                    case,
                    item=item,
                    author=author or None,
                    kind=kind or None,
                    entity=entity or None,
                    include_raw=bool(include_raw),
                    limit=int(limit),
                )
            },
        )

    @app.post("/v1/board/journal")
    def board_journal_append(request: Request, body: dict):
        body = body or {}
        return _board_auxiliary(
            request,
            lambda actor: manager.journal_store.append(
                actor,
                str(body.get("case", "")),
                str(body.get("body", "")),
                kind=str(body.get("kind") or "note"),
                space=str(body.get("space") or "") or None,
                item=int(body["item"]) if body.get("item") is not None else None,
                entities=[str(e) for e in body.get("entities") or []],
                refs=[str(ref) for ref in body.get("refs") or []],
            ),
        )

    @app.get("/v1/memory")
    def memory() -> dict[str, Any]:
        return {"memory": manager.list_memory()}

    @app.post("/v1/memory")
    def add_memory(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_memory(
            str(body.get("content", "")), str(body.get("scope", "workspace"))
        )

    # Declared before the /{item_id} routes so "settings" can never be parsed as an id.
    @app.get("/v1/memory/settings")
    def memory_settings() -> dict[str, Any]:
        return manager.get_memory_settings()

    @app.put("/v1/memory/settings")
    def memory_settings_put(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.set_memory_settings(
            enabled=bool(body["enabled"]) if "enabled" in body else None,
            user_rules=str(body["user_rules"]) if "user_rules" in body else None,
        )

    @app.patch("/v1/memory/{item_id}")
    def memory_patch(item_id: int, body: dict) -> dict[str, Any]:
        return manager.update_memory(item_id, str((body or {}).get("content", "")))

    @app.delete("/v1/memory/{item_id}")
    def memory_delete(item_id: int) -> dict[str, Any]:
        return manager.delete_memory(item_id)

    @app.delete("/v1/memory")
    def memory_delete_all() -> dict[str, Any]:
        return manager.delete_all_memory()

    # -- project bindings (pass 20 / UX-044) --------------------------------------

    @app.get("/v1/sessions/{session_id}/project-menu")
    def project_menu(session_id: str, kind: str = "memory") -> dict[str, Any]:
        return manager.project_menu(session_id, kind)

    @app.put("/v1/sessions/{session_id}/bindings")
    def put_binding(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        name = body.get("name")
        return manager.set_binding(
            session_id, str(body.get("kind", "")), str(name) if name else None
        )

    @app.post("/v1/sessions/{session_id}/project-name")
    def name_project(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.name_current_project(
            session_id, str(body.get("kind", "")), str(body.get("name", ""))
        )

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict) -> dict[str, Any]:
        model = body.get("model", manager.model)
        turn = manager.provider_complete(
            model, body.get("messages", []), body.get("tools")
        )
        return _openai_response(model, turn)

    # -- MCP servers ------------------------------------------------------------
    @app.get("/v1/mcp")
    def mcp_list() -> dict[str, Any]:
        return {"servers": manager.list_mcp()}

    @app.post("/v1/mcp")
    def mcp_add(body: dict) -> dict[str, Any]:
        name = body.get("name")
        config = body.get("config")
        if not name or not isinstance(config, dict):
            return {"ok": False, "error": "name and config required"}
        return manager.add_mcp(name, config)

    @app.patch("/v1/mcp/{name}")
    def mcp_patch(name: str, body: dict) -> dict[str, Any]:
        return manager.patch_mcp(name, body or {})

    @app.delete("/v1/mcp/{name}")
    def mcp_delete(name: str) -> dict[str, Any]:
        return manager.delete_mcp(name)

    @app.get("/v1/mcp/{name}/tools")
    async def mcp_tools(name: str) -> dict[str, Any]:
        return await manager.mcp_tools(name)

    @app.post("/v1/mcp/{name}/connect")
    async def mcp_connect(name: str) -> dict[str, Any]:
        # Connect now. For `auth: oauth` servers the first connect opens the system
        # browser and waits on the loopback callback — that can take minutes, so it
        # runs as a background task; the GUI polls /v1/mcp for the status flip
        # (authorizing → connected | needs_auth + last_error).
        manager.begin_mcp_connect(name)  # authorizing shows on the very next poll
        asyncio.create_task(manager.connect_mcp(name))
        return {"ok": True, "started": True}

    @app.post("/v1/mcp/{name}/signout")
    async def mcp_signout(name: str) -> dict[str, Any]:
        return await manager.signout_mcp(name)

    @app.get("/mcp/oauth/callback")
    async def mcp_oauth_callback(
        code: str = "", state: str = "", error: str = ""
    ) -> Any:
        # Loopback landing for the MCP OAuth browser flow (mcp/oauth.py). Browser-facing:
        # returns the same styled page as the managed-connector callbacks.
        from fastapi.responses import HTMLResponse

        from ..mcp import oauth as mcp_oauth

        if error:
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed",
                    "The service reported an error. Return to Astra and try again.",
                    ok=False,
                    error=error,
                ),
                status_code=400,
            )
        if not code or not mcp_oauth.deliver_callback(code, state or None):
            return HTMLResponse(
                _browser_page(
                    "Nothing waiting for this sign-in",
                    "The sign-in may have timed out. Return to Astra and start it again.",
                    ok=False,
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                "Connected",
                "Sign-in complete. You can close this tab and return to Astra.",
                ok=True,
            )
        )

    @app.post("/v1/mcp/reload")
    async def mcp_reload() -> dict[str, Any]:
        return await manager.reload_mcp()

    # -- connectors (Slack / Telegram / …) --------------------------------------
    @app.get("/v1/connectors")
    def connectors_list() -> dict[str, Any]:
        return {"connectors": manager.list_connectors()}

    async def _refresh_listeners_if_two_way(name: str) -> None:
        # New/removed creds only take effect when the platform socket reconnects (Socket Mode
        # authenticates at connect time) — hot-reload the listeners in-process so pasting
        # tokens works immediately, no sidecar restart (§19).
        from ..connectors.config import PLATFORMS

        if name in PLATFORMS:
            try:
                await manager.refresh_gateway()
            except Exception:
                pass  # a listener that fails to come up must not fail the save

    @app.post("/v1/connectors/{name}/connect")
    async def connector_connect(name: str, body: dict) -> dict[str, Any]:
        fields = body.get("fields") if isinstance(body, dict) else None
        # experimental connectors require the caller to explicitly acknowledge the risk notice
        acknowledged = bool(isinstance(body, dict) and body.get("acknowledge_risk"))
        # token validation does a blocking HTTP call → keep it off the event loop
        result = await asyncio.to_thread(
            lambda: manager.connect_connector(
                name, fields or {}, acknowledged=acknowledged
            )
        )
        if result.get("ok"):
            await _refresh_listeners_if_two_way(name)
        return result

    @app.post("/v1/connectors/gmail/connect-google")
    async def gmail_connect_google() -> dict[str, Any]:
        """Start Astra-owned Google OAuth without a connector broker."""
        import webbrowser

        from .. import google_oauth
        from ..config import load_config

        result = google_oauth.begin_gmail_connect(load_config())
        if result.get("ok"):
            webbrowser.open(result["authorize_url"])
        return result

    @app.post("/v1/connectors/gmail/open-google-oauth-setup")
    async def gmail_open_google_oauth_setup() -> dict[str, Any]:
        """Open the operator setup page when this Astra install lacks a Google client."""
        import webbrowser

        from .. import google_oauth
        from ..config import load_config

        setup_url = "https://console.cloud.google.com/apis/credentials"
        try:
            webbrowser.open(setup_url)
        except Exception:
            return {"ok": False, "error": "could not open Google Cloud"}
        return {
            "ok": True,
            "setup_url": setup_url,
            "redirect_uri": google_oauth.redirect_uri(load_config()),
        }

    @app.get("/oauth/google/callback")
    async def gmail_google_callback(code: str = "", state: str = "", error: str = "") -> Any:
        from fastapi.responses import HTMLResponse

        from .. import google_oauth
        from ..config import load_config

        if error:
            return HTMLResponse(
                _browser_page(
                    "Google sign-in failed",
                    "Google did not grant Gmail access. Return to Astra and try again.",
                    ok=False,
                    error=error,
                    connector="gmail",
                ),
                status_code=400,
            )
        result = await asyncio.to_thread(
            lambda: google_oauth.complete_gmail_connect(
                manager.secrets, load_config(), code=code, state=state
            )
        )
        if not result.get("ok"):
            return HTMLResponse(
                _browser_page(
                    "Google sign-in failed",
                    "Astra could not connect this Gmail account. Return to Astra and try again.",
                    ok=False,
                    error=str(result.get("error") or "unknown error"),
                    connector="gmail",
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                "Gmail connected",
                "You can close this tab and return to Astra.",
                connector="gmail",
            )
        )

    @app.post("/v1/connectors/{name}/mcp-connect")
    async def connector_mcp_connect(name: str) -> dict[str, Any]:
        # One-click connect for an MCP-backed connector: the browser OAuth flow can
        # take minutes, so it runs in the background; the GUI polls /v1/connectors
        # until the card flips to connected (mode "mcp").
        from ..connectors.descriptors import get_descriptor

        d = get_descriptor(name)
        if d is None or not d.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        asyncio.create_task(manager.mcp_connect_connector(name))
        return {"ok": True, "started": True}

    @app.post("/v1/connectors/{name}/disconnect")
    async def connector_disconnect(name: str) -> dict[str, Any]:
        # Managed profiles: best-effort flip of the cloud metadata record first
        # (network call → off the loop). Local deletion always proceeds.
        from .. import cloud
        from ..config import load_config

        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(manager.secrets, load_config(), name)
        )
        result = manager.disconnect_connector(name)
        await _refresh_listeners_if_two_way(name)
        return result

    @app.post("/v1/connectors/slack/workspaces/{team_id}/disconnect")
    async def slack_workspace_disconnect(team_id: str) -> dict[str, Any]:
        """Stop relaying one workspace (managed relay). Cloud routing row deleted
        best-effort, local per-team token removed, gateway hot-reloaded."""
        return await manager.disconnect_slack_workspace(team_id)

    @app.get("/v1/connectors/slack/status")
    async def slack_status() -> dict[str, Any]:
        """Slack health, three layers: relay socket / cloud sign-in / per-team tokens."""
        return manager.slack_status()

    @app.post("/v1/connectors/github/installations/{installation_id}/disconnect")
    async def github_installation_disconnect(installation_id: str) -> dict[str, Any]:
        """Stop relaying one GitHub App installation (managed relay). Cloud
        routing rows deleted best-effort, local profile removed, gateway
        hot-reloaded."""
        return await manager.disconnect_github_installation(installation_id)

    @app.get("/v1/connectors/github/status")
    async def github_status() -> dict[str, Any]:
        """GitHub health: relay socket / cloud sign-in / per-installation tokens."""
        return manager.github_status()

    @app.post("/v1/connectors/gmail/accounts/{email}/disconnect")
    async def gmail_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE mailbox (cloud metadata best-effort first, like a full
        disconnect); the default pointer moves to the next account."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import gmail_accounts

        profile_key = gmail_accounts.PREFIX + email.strip().lower()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets, load_config(), "gmail", profile_key=profile_key
            )
        )
        return gmail_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/gmail/accounts/{email}/default")
    def gmail_account_default(email: str) -> dict[str, Any]:
        from ..connectors import gmail_accounts

        return gmail_accounts.set_default(manager.secrets, email)

    @app.patch("/v1/connectors/gmail/filters")
    def gmail_filters(body: dict) -> dict[str, Any]:
        """Replace the "Never show agents" lists. Enforced in the local tool
        layer; agents see silent omissions, the user sees counts + audit."""
        from ..connectors import gmail_accounts

        senders = body.get("senders") if isinstance(body, dict) else None
        labels = body.get("labels") if isinstance(body, dict) else None
        if senders is not None and not isinstance(senders, list):
            return {"ok": False, "error": "senders must be a list"}
        if labels is not None and not isinstance(labels, list):
            return {"ok": False, "error": "labels must be a list"}
        return gmail_accounts.set_filters(manager.secrets, senders, labels)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/disconnect")
    async def gcal_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE Google Calendar account (cloud metadata best-effort first);
        the default pointer moves to the next account."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import gcal_accounts

        profile_key = gcal_accounts.PREFIX + email.strip().lower()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets,
                load_config(),
                "google_calendar",
                profile_key=profile_key,
            )
        )
        return gcal_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/default")
    def gcal_account_default(email: str) -> dict[str, Any]:
        from ..connectors import gcal_accounts

        return gcal_accounts.set_default(manager.secrets, email)

    @app.post("/v1/connectors/hubspot/portals/{hub_id}/disconnect")
    async def hubspot_portal_disconnect(hub_id: str) -> dict[str, Any]:
        from .. import cloud
        from ..config import load_config
        from ..connectors import hubspot_portals

        profile_key = hubspot_portals.PREFIX + hub_id.strip()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets, load_config(), "hubspot", profile_key=profile_key
            )
        )
        return hubspot_portals.disconnect_portal(manager.secrets, hub_id)

    @app.post("/v1/connectors/hubspot/portals/{hub_id}/default")
    def hubspot_portal_default(hub_id: str) -> dict[str, Any]:
        from ..connectors import hubspot_portals

        return hubspot_portals.set_default(manager.secrets, hub_id)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/disconnect")
    async def account_disconnect(name: str, account_id: str) -> dict[str, Any]:
        """Generic per-account disconnect for account-patterned connectors
        (batch 2+). Gmail/Calendar keep their specific email routes."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        _id, profile_key, profile = accounts.resolve(manager.secrets, name, account_id)
        if profile and profile.get("managed"):
            await asyncio.to_thread(
                lambda: cloud.cloud_disconnect(
                    manager.secrets, load_config(), name, profile_key=profile_key
                )
            )
        return accounts.disconnect_account(manager.secrets, name, account_id)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/default")
    def account_default(name: str, account_id: str) -> dict[str, Any]:
        from ..connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        return accounts.set_default(manager.secrets, name, account_id)

    @app.patch("/v1/connectors/hubspot/hidden-fields")
    def hubspot_hidden_fields(body: dict) -> dict[str, Any]:
        """Replace the hidden-fields denylist (property names stripped from every
        record agents read — model-facing policy, not a human ACL)."""
        from ..connectors import hubspot_portals

        fields = body.get("hidden_fields") if isinstance(body, dict) else None
        if not isinstance(fields, list):
            return {"ok": False, "error": "hidden_fields must be a list"}
        return hubspot_portals.set_hidden_fields(manager.secrets, fields)

    @app.post("/v1/connectors/{name}/unauthorized/{item_id}")
    async def connector_unauthorized_resolve(
        name: str, item_id: str, body: dict
    ) -> dict[str, Any]:
        # Resolve a parked unauthorized message: dismiss / allow / allow_deliver (§19).
        action = str((body or {}).get("action", "")).strip()
        return await manager.resolve_unauthorized(name, item_id, action)

    # -- Astra Connect: sign-in + managed one-click connect ------------------
    # All optional: the app is fully functional signed out (manual token paste
    # stays available for every connector, before and after sign-in).

    @app.get("/v1/cloud/status")
    def cloud_status() -> dict[str, Any]:
        from .. import cloud

        return {
            **cloud.status(manager.secrets),
            "telemetry_enabled": cloud.telemetry_enabled(manager.secrets),
        }

    @app.post("/v1/cloud/telemetry")
    def cloud_telemetry(body: dict) -> dict[str, Any]:
        """The Phase 5 opt-out toggle. Local preference only — signed-out users
        send nothing regardless of this value."""
        from .. import cloud

        return cloud.set_telemetry_enabled(
            manager.secrets, bool((body or {}).get("enabled", True))
        )

    @app.post("/v1/cloud/login")
    def cloud_login() -> dict[str, Any]:
        """Start browser sign-in. The sidecar opens the system browser itself
        (works identically under Tauri and plain-browser dev)."""
        import webbrowser

        from .. import cloud
        from ..config import load_config

        out = cloud.begin_login(load_config())
        webbrowser.open(out["authorize_url"])
        return {"ok": True, "authorize_url": out["authorize_url"]}

    @app.post("/v1/cloud/logout")
    def cloud_logout() -> dict[str, Any]:
        from .. import cloud

        return cloud.logout(manager.secrets)

    @app.get("/auth/callback")
    async def cloud_auth_callback(code: str = "", state: str = "", error: str = ""):
        from fastapi.responses import HTMLResponse

        from .. import cloud
        from ..config import load_config

        signin_failed_detail = (
            "Close this tab and try signing in again from Astra."
        )
        if error:
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed", signin_failed_detail, ok=False, error=error
                ),
                status_code=400,
            )
        result = await asyncio.to_thread(
            lambda: cloud.complete_login(manager.secrets, load_config(), code, state)
        )
        if not result.get("ok"):
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed",
                    signin_failed_detail,
                    ok=False,
                    error=result.get("error", ""),
                ),
                status_code=400,
            )

        # A cloud sign-in can still be used for optional surfaces such as Gallery,
        # but it must not restore connector profiles unless the owner explicitly
        # enabled their managed broker. This prevents a local sign-in from silently
        # reactivating a legacy Astra-managed connection.
        async def _restore_connections() -> None:
            try:
                out = await asyncio.to_thread(
                    lambda: cloud.sync_connections(manager.secrets, load_config())
                )
                if out.get("restored"):
                    await manager.refresh_gateway()
            except Exception:
                pass  # sign-in stands; the user can still connect by hand

        if load_config().managed_connectors_enabled:
            asyncio.get_running_loop().create_task(_restore_connections())
        return HTMLResponse(
            _browser_page(
                "Signed in",
                "You're signed in to Astra Connect. "
                "You can close this tab and return to Astra.",
            )
        )

    @app.post("/v1/connectors/{name}/connect-managed")
    async def connector_connect_managed(
        name: str, body: Optional[dict] = None
    ) -> dict[str, Any]:
        """One-click managed OAuth (requires cloud sign-in). Opens the provider
        consent page in the system browser; the broker's callback page will
        form-POST the tokens to /oauth/callback below. `access` picks a consent
        tier by NAME (e.g. hubspot read | write) — the broker owns the scopes."""
        import webbrowser

        from .. import cloud
        from ..config import load_config
        from ..connectors.descriptors import get_descriptor

        config = load_config()
        d = get_descriptor(name)
        if not config.managed_connectors_enabled:
            return {
                "ok": False,
                "error": "managed one-click connections are disabled; connect manually instead",
            }
        if d is None or not d.managed:
            return {"ok": False, "error": f"{name} has no managed connect path"}
        if d.managed_paused:
            # GUI shows the Coming-soon state; this guard covers stale GUIs/API callers.
            return {
                "ok": False,
                "error": f"one-click connect for {d.title} is coming soon — connect manually for now",
            }
        access = str((body or {}).get("access") or "")
        flow = str((body or {}).get("flow") or "")  # github: "" install | "authorize"
        out = await asyncio.to_thread(
            lambda: cloud.begin_managed_connect(
                manager.secrets, config, name, access=access, flow=flow
            )
        )
        if out.get("ok"):
            webbrowser.open(out["authorize_url"])
        return out

    @app.post("/oauth/callback")
    async def managed_oauth_callback(request: Request) -> Any:
        from fastapi.responses import HTMLResponse

        from .. import cloud
        from ..config import load_config
        from ..connectors.descriptors import get_descriptor
        from ..connectors.setup import (
            managed_connect_connector,
            managed_connect_slack_install,
        )

        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
        connector = data.get("connector", "")
        if not cloud.consume_managed_state(data.get("app_state", "")):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error="unknown or expired connection attempt",
                ),
                status_code=400,
            )
        if data.get("error"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error=data["error"],
                ),
                status_code=400,
            )
        descriptor = get_descriptor(connector)
        if (
            not load_config().managed_connectors_enabled
            or descriptor is None
            or not descriptor.managed
        ):
            return HTMLResponse(
                _browser_page(
                    "Connection disabled",
                    "Managed one-click connections are disabled in this Astra install. "
                    "Connect this app manually instead.",
                    ok=False,
                ),
                status_code=400,
            )
        # Managed GitHub deliberately carries NO token fields — the loopback POST
        # is routing metadata only (installation tokens are minted on demand,
        # github-relay-spec §4) — so its branch precedes the access_token check.
        if connector == "github" and data.get("installation_id"):
            from ..connectors.github_installs import managed_connect_install

            result = managed_connect_install(manager.secrets, data)
            if result.get("ok"):
                await manager.refresh_gateway()  # hot-add, like a workspace
            if not result.get("ok"):
                return HTMLResponse(
                    _browser_page(
                        "Connection failed",
                        _CONNECT_FAILED_DETAIL,
                        ok=False,
                        error=result.get("error", ""),
                    ),
                    status_code=400,
                )
            return HTMLResponse(
                _browser_page(
                    "GitHub connected",
                    "You can close this tab and return to Astra.",
                    connector="github",
                )
            )
        if not connector or not data.get("access_token"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error="missing fields",
                ),
                status_code=400,
            )
        # Managed Slack is multi-workspace + relay: store the per-team bot token
        # and flip to relay mode, rather than the single-token connector path.
        if connector == "slack" and data.get("team_id"):
            result = managed_connect_slack_install(manager.secrets, data)
            if result.get("ok"):
                # Hot-add: rebuild the gateway so the new workspace's token loads
                # (and the relay socket opens on a first-ever install) right away.
                await manager.refresh_gateway()
        elif connector == "gmail":
            # Multi-account: each sign-in lands in its own gmail:account:<email>
            # profile; the first becomes the default mailbox.
            from ..connectors import gmail_accounts

            result = gmail_accounts.managed_connect_account(
                manager.secrets, cloud.managed_profile_from_callback(data)
            )
        elif connector == "google_calendar":
            # Multi-account, same shape as gmail: google_calendar:account:<email>.
            from ..connectors import gcal_accounts

            result = gcal_accounts.managed_connect_account(
                manager.secrets, cloud.managed_profile_from_callback(data)
            )
        elif connector == "hubspot" and data.get("hub_id"):
            # Multi-portal: keyed by hub_id (broker sends it like Slack's team_id).
            from ..connectors import hubspot_portals

            profile = cloud.managed_profile_from_callback(data)
            profile["hub_id"] = data.get("hub_id", "")
            if data.get("sandbox"):
                profile["sandbox"] = True
            result = hubspot_portals.managed_connect_portal(manager.secrets, profile)
        else:
            result = managed_connect_connector(
                manager.secrets, connector, cloud.managed_profile_from_callback(data)
            )
        if not result.get("ok"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error=result.get("error", ""),
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                f"{_connector_title(connector)} connected",
                "You can close this tab and return to Astra.",
                connector=connector,
            )
        )

    @app.patch("/v1/connectors/{name}/tools")
    def connector_tools_patch(name: str, body: dict) -> dict[str, Any]:
        enabled = (body or {}).get("enabled")
        if not isinstance(enabled, dict):
            return {"ok": False, "error": "enabled map required"}
        return manager.update_connector_tools(name, enabled)

    @app.post("/v1/connectors/{name}/allow")
    def connector_allow(name: str, body: dict) -> dict[str, Any]:
        # `team_id` scopes the edit to one workspace (managed relay); absent → flat list.
        # `name` (optional) seeds the people directory so a directory-picked user's
        # chip shows their display name before they've ever sent a message.
        return manager.allow_user(
            name,
            str(body.get("user_id", "")),
            str(body.get("team_id", "")) or None,
            display_name=str(body.get("name", "")),
        )

    @app.get("/v1/connectors/slack/workspaces/{team_id}/directory")
    async def slack_directory(
        team_id: str, q: str = "", limit: int = 25
    ) -> dict[str, Any]:
        """Workspace member roster for the people picker (team_id "default" =
        the manual Socket-Mode workspace). Cached locally; never leaves this machine."""
        from ..connectors import slack_directory as roster

        return await asyncio.to_thread(
            lambda: roster.list_members(manager.secrets, team_id, q, limit)
        )

    @app.get("/v1/connectors/slack/workspaces/{team_id}/channels")
    async def slack_channels(
        team_id: str, q: str = "", limit: int = 25
    ) -> dict[str, Any]:
        """Channel roster for the channel typeahead: all public channels, private
        ones only where the bot is a member (Slack API constraint)."""
        from ..connectors import slack_directory as roster

        return await asyncio.to_thread(
            lambda: roster.list_channels(manager.secrets, team_id, q, limit)
        )

    @app.post("/v1/connectors/{name}/disallow")
    def connector_disallow(name: str, body: dict) -> dict[str, Any]:
        return manager.disallow_user(
            name, str(body.get("user_id", "")), str(body.get("team_id", "")) or None
        )

    @app.post("/v1/connectors/slack/approval-owners/add")
    def slack_approval_owner_add(body: dict) -> dict[str, Any]:
        return manager.set_slack_approval_owner(
            str(body.get("user_id", "")),
            add=True,
            display_name=str(body.get("name", "")),
        )

    @app.post("/v1/connectors/slack/approval-owners/remove")
    def slack_approval_owner_remove(body: dict) -> dict[str, Any]:
        return manager.set_slack_approval_owner(
            str(body.get("user_id", "")), add=False
        )

    # -- audit / browser observability ------------------------------------------
    @app.get("/v1/audit")
    def audit_list(
        limit: int = 100,
        session_id: str | None = None,
        connector: str | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        return {
            "events": manager.list_audit(
                limit=limit, session_id=session_id, connector=connector, tool=tool
            )
        }

    @app.get("/v1/browser/state")
    def browser_state_get() -> dict[str, Any]:
        return manager.browser_state()

    @app.post("/v1/browser/screenshot")
    def browser_screenshot_post() -> dict[str, Any]:
        return manager.browser_screenshot()

    @app.post("/v1/browser/close")
    def browser_close_post() -> dict[str, Any]:
        return manager.browser_close()

    # -- web search -------------------------------------------------------------
    @app.get("/v1/web-search")
    def web_search_get() -> dict[str, Any]:
        return manager.get_web_search()

    @app.post("/v1/web-search/query")
    async def web_search_query_post(body: dict) -> dict[str, Any]:
        query = str((body or {}).get("query", "")).strip()
        if not query:
            return {"ok": False, "results": [], "error": "query required"}
        from ..web import make_web_search_tool
        tool = make_web_search_tool(manager.secrets)
        res = await asyncio.to_thread(tool, query=query, max_results=10)
        return {"ok": True, "results": res.get("results", []) if isinstance(res, dict) else []}

    @app.post("/v1/web-search")
    def web_search_set(body: dict) -> dict[str, Any]:
        provider = (body or {}).get("provider", "")
        if not provider:
            return {"ok": False, "error": "provider required"}
        return manager.set_web_search(provider, (body or {}).get("api_key"))
    @app.get("/v1/providers")
    def providers_get() -> list[dict[str, Any]]:
        return manager.get_providers()

    @app.post("/v1/providers")
    def providers_set(body: dict) -> dict[str, Any]:
        name = (body or {}).get("name", "")
        if not name:
            return {"ok": False, "error": "name required"}
        return manager.set_provider(name, (body or {}).get("fields"))

    @app.delete("/v1/providers/{name}")
    def providers_remove(name: str) -> dict[str, Any]:
        return manager.remove_provider(name)

    @app.post("/v1/providers/verify")
    async def providers_verify(body: dict) -> dict[str, Any]:
        # Live read-only credential check (sync httpx) — run off the event loop.
        name = (body or {}).get("name", "") or "openai"
        return await asyncio.to_thread(
            manager.verify_provider, name, (body or {}).get("fields")
        )

    @app.post("/v1/providers/openai-codex/signin")
    async def codex_signin() -> dict[str, Any]:
        # Opens the system browser and waits on the loopback callback — that can
        # take minutes, so it runs as a background task; the GUI polls the status
        # route for the flip (authorizing → signed_in | last_error). Same shape as
        # the MCP OAuth connect route.
        manager.begin_codex_signin()
        asyncio.create_task(manager.codex_signin())
        return {"ok": True, "started": True}

    @app.get("/v1/providers/openai-codex/status")
    def codex_status() -> dict[str, Any]:
        return manager.codex_status()

    @app.post("/v1/providers/openai-codex/signout")
    def codex_signout() -> dict[str, Any]:
        return manager.codex_signout()

    # -- settings (model API key) -----------------------------------------------
    @app.get("/v1/settings")
    def settings_get() -> dict[str, Any]:
        return manager.get_settings()

    @app.post("/v1/settings/model-key")
    def settings_set_model_key(body: dict) -> dict[str, Any]:
        return manager.set_model_key((body or {}).get("api_key", ""))

    @app.post("/v1/settings/default-model")
    def settings_set_default_model(body: dict) -> dict[str, Any]:
        return manager.set_default_model((body or {}).get("model", ""))

    @app.post("/v1/settings/models/add")
    def settings_models_add(body: dict) -> dict[str, Any]:
        return manager.add_model((body or {}).get("model", ""))

    @app.post("/v1/settings/models/remove")
    def settings_models_remove(body: dict) -> dict[str, Any]:
        return manager.remove_model((body or {}).get("model", ""))

    @app.post("/v1/settings/onboarded")
    def settings_set_onboarded(body: dict) -> dict[str, Any]:
        return manager.set_onboarded(bool((body or {}).get("value", True)))

    @app.post("/v1/settings/experimental-connectors")
    def settings_set_experimental(body: dict) -> dict[str, Any]:
        return manager.set_experimental_connectors(bool((body or {}).get("value")))

    @app.post("/v1/settings/surfaces")
    def settings_set_surfaces(body: dict) -> dict[str, Any]:
        b = body or {}
        return manager.set_surfaces(chat=b.get("chat"), code=b.get("code"))

    @app.post("/v1/settings/scratch-base")
    def settings_set_scratch_base(body: dict) -> dict[str, Any]:
        return manager.set_scratch_base(str((body or {}).get("path", "")))

    @app.post("/v1/settings/nav-layout")
    def settings_set_nav_layout(body: dict) -> dict[str, Any]:
        return manager.set_nav_layout(str((body or {}).get("nav_layout", "")))

    @app.post("/v1/settings/sessions-peek")
    def settings_set_sessions_peek(body: dict) -> dict[str, Any]:
        # Sidebar: sessions shown per group before "Show more" (owner ask, 2026-07-03).
        return manager.set_sessions_peek((body or {}).get("sessions_peek", 5))

    @app.post("/v1/settings/context-bar")
    def settings_set_context_bar(body: dict) -> dict[str, Any]:
        # Composer: show the context-window fill bar, or just the popover (owner ask).
        return manager.set_context_bar((body or {}).get("context_bar", True))

    @app.post("/v1/settings/auto-approve")
    def settings_set_auto_approve(body: dict) -> dict[str, Any]:
        # Auto-Approve feature flag (spec §1.5): when on, Mode.AUTO_APPROVE gets an LLM
        # reviewer. Takes effect on the next session build. Turning it off leaves any
        # shadow-eval setting alone (they are independent switches).
        return manager.set_auto_approve((body or {}).get("auto_approve", False))

    @app.post("/v1/settings/auto-approve-shadow")
    def settings_set_auto_approve_shadow(body: dict) -> dict[str, Any]:
        # Shadow evaluation (Part 6 step 3): the reviewer records what it WOULD decide on
        # every approval card while the human still decides. Independent of the live flag.
        return manager.set_auto_approve_shadow((body or {}).get("auto_approve_shadow", False))

    @app.post("/v1/settings/pdf")
    def settings_set_pdf(body: dict) -> dict[str, Any]:
        # Token savings (owner ask, 2026-07-17): fallback mode for models without native
        # PDF support + attach-time page/size thresholds.
        b = body or {}
        return manager.set_pdf_settings(
            fallback=b.get("pdf_fallback"),
            max_pages=b.get("pdf_max_pages"),
            max_mb=b.get("pdf_max_mb"),
        )

    @app.post("/v1/settings/compaction")
    def settings_set_compaction(body: dict) -> dict[str, Any]:
        # Auto-compaction overrides (OPE-27): threshold % of the context window, the
        # absolute token cap, and the summarizer-model pin ("" → session's own model).
        b = body or {}
        return manager.set_compaction_settings(
            threshold_pct=b.get("compaction_threshold_pct"),
            cap_tokens=b.get("compaction_cap_tokens"),
            model=b.get("compaction_model"),
        )

    @app.post("/v1/attachments/inspect-pdf")
    def attachments_inspect_pdf(body: dict) -> dict[str, Any]:
        # Attach-time page/size probe for the composer's threshold check. Local only.
        from ..pdf_support import inspect

        return inspect(str((body or {}).get("data_url", "")))

    # -- direct-message routing -------------------------------------------------
    @app.get("/v1/messaging/dm-route")
    def dm_route_get() -> dict[str, Any]:
        return {"dm_session": manager.dm_session()}

    @app.post("/v1/messaging/dm-route")
    def dm_route_set(body: dict) -> dict[str, Any]:
        # A falsy session_id clears the designation (DMs then park as unrouted).
        return manager.set_dm_session((body or {}).get("session_id", ""))

    # -- remote & ssh runner ---------------------------------------------------
    @app.get("/v1/remote/ssh/profiles")
    def remote_ssh_profiles_list() -> dict[str, Any]:
        return {"profiles": manager.list_ssh_profiles()}

    @app.post("/v1/remote/ssh/profiles")
    def remote_ssh_profiles_save(body: dict) -> dict[str, Any]:
        return manager.save_ssh_profile(body or {})

    @app.delete("/v1/remote/ssh/profiles/{profile_id}")
    def remote_ssh_profiles_delete(profile_id: str) -> dict[str, Any]:
        return manager.delete_ssh_profile(profile_id)

    @app.post("/v1/remote/ssh/active")
    def remote_ssh_active_set(body: dict) -> dict[str, Any]:
        return manager.set_active_ssh_profile((body or {}).get("profile_id", ""))

    @app.post("/v1/remote/ssh/test")
    def remote_ssh_test(body: dict) -> dict[str, Any]:
        return manager.test_ssh_profile(body or {})

    @app.get("/v1/remote/status")
    def remote_status() -> dict[str, Any]:
        return manager.remote_server_status()

    if os.environ.get("COWORKER_DEBUG_INJECT") == "1":
        # Dev-only (env-gated, localhost): feed a message through the real inbound path so the
        # messaging stack can be exercised without a live bot connection. Not registered otherwise.
        @app.post("/v1/_debug/inject_inbound")
        async def debug_inject_inbound(body: dict) -> dict[str, Any]:
            from ..connectors.base import MessageEvent, SessionSource

            event = MessageEvent(
                text=str((body or {}).get("text", "")),
                source=SessionSource(
                    platform=str(body.get("platform", "slack")),
                    chat_id=str(body.get("chat_id", "C0BD7KZ1AH5")),
                    user_id=str(body.get("user_id", "U07JK68S4BH")),
                    user_name=str(body.get("user_name", "tester")),
                    chat_type=str(body.get("chat_type", "channel")),
                    chat_name=str(body.get("chat_name", "")) or None,
                    thread_id=str(body.get("thread_ts", "")) or None,
                    team_id=str(body.get("team_id", "")) or None,
                ),
                message_id=str(body.get("ts", "")) or None,
                # §31 mention router: the flag is normally computed from the raw Slack text
                # at mapping time; the injector sets it directly.
                mentions_me=bool(body.get("mentions_me")),
            )
            await manager._dispatch_inbound(event)
            return {"ok": True}

    # -- automations (scheduled tasks) ------------------------------------------
    @app.get("/v1/automations")
    def automations_list() -> dict[str, Any]:
        return manager.list_automations()

    @app.post("/v1/automations")
    def automations_create(body: dict) -> dict[str, Any]:
        return manager.create_automation(body or {})

    @app.get("/v1/automations/{task_id}")
    def automation_get(task_id: str) -> dict[str, Any]:
        return manager.get_automation(task_id)

    @app.patch("/v1/automations/{task_id}")
    def automation_update(task_id: str, body: dict) -> dict[str, Any]:
        return manager.update_automation(task_id, body or {})

    @app.delete("/v1/automations/{task_id}")
    def automation_delete(task_id: str) -> dict[str, Any]:
        return manager.delete_automation(task_id)

    @app.post("/v1/automations/{task_id}/seen")
    def automations_seen(task_id: str) -> dict[str, Any]:
        return manager.mark_automation_seen(task_id)

    @app.post("/v1/automations/{task_id}/run")
    def automation_run(task_id: str) -> dict[str, Any]:
        # Prepare a live manual run; the GUI opens the returned session and drives it.
        return manager.prepare_manual_run(task_id)

    @app.post("/v1/automations/{task_id}/runs/{run_id}/finalize")
    def automation_run_finalize(task_id: str, run_id: str) -> dict[str, Any]:
        return manager.finalize_manual_run(task_id, run_id)

    @app.websocket("/ws/session/{session_id}")
    async def ws_session(ws: WebSocket, session_id: str) -> None:
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        # CORS never gates WebSockets, so a cross-site page could otherwise open this socket
        # and drive the session into tool calls. Reject a disallowed browser Origin before
        # accepting the handshake (1008 = policy violation).
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol=_websocket_protocol(ws))
        agent = ws.query_params.get("agent") or "code"

        # All four interactive prompts (approval / question / directory / plan) are parked as Inbox
        # items and awaited via inbox.wait — so they survive a dropped socket (redelivered on
        # reconnect) and can be resolved from any surface. `visibility` decides where they SHOW:
        # Unattended → the cross-session Inbox; attended → inline in this session only. The agent
        # stays blocked until the item is resolved (live WS response, REST, or a bound channel).
        def _visibility() -> str:
            return (
                VIS_INBOX
                if manager.unattended.is_unattended(session_id)
                else VIS_INLINE
            )

        async def _mirror(item) -> None:
            # Unattended items mirror to a bound channel as buttons (see mirror_inbox_item).
            await manager.mirror_inbox_item(item)

        def _route() -> str:
            return manager.inbox_routing.route_for(session_id, agent)

        async def approver(_request) -> ApprovalOutcome:
            # The engine has already emitted PERMISSION_REQUIRED (the live inline card). Park the
            # item so the answer can also come from the Inbox / a reconnect / after a restart.
            item = manager.inbox.add_approval(
                session_id,
                f"Run `{_request.tool_name}`?",
                body="\n".join(
                    p
                    for p in (
                        (getattr(_request, "reason", "") or "").strip(),
                        args_preview(getattr(_request, "arguments", None)),
                    )
                    if p
                ),
                inbox=_route(),
                visibility=_visibility(),
                # Automation-run context (manual "Run now" rides this socket): lets the
                # card offer the task-persistent "Allow every time" (§25). {} elsewhere.
                data=manager.approval_prompt_data(session_id, _request),
                tool_call_id=getattr(_request, "tool_call_id", None),
            )
            if (
                item.state == "pending"
            ):  # freshly raised (not a durable-resume re-raise)
                manager.persist_session(
                    session_id
                )  # the pending tool call is now on disk
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resolution = await manager.inbox.wait(item.id)
            # Accept every vocabulary: the live card sends once/always_tool/always_command/
            # always_task/deny; the Inbox / a channel send allow/always/deny.
            return manager.approval_outcome(resolution, _request, session_id)

        async def question_asker(args: dict, tool_call_id=None) -> dict:
            # ask_user (engine does NOT emit the event — we do, only when attended).
            from ..tools.ask import answer_result, question_item_fields

            fields = question_item_fields(args)
            if fields is None:  # engine guards too; belt-and-braces
                return {"answer": "", "error": "no question"}
            item = manager.inbox.add_question(
                session_id,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
                **fields,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
                else:
                    await manager.broadcast_session(
                        session_id,
                        {
                            "type": "question_requested",
                            "data": {
                                "question": item.title,
                                "options": item.options,
                                "allow_text": item.allow_text,
                                "multi": item.multi,
                                "header": item.header,
                                "questions": item.questions,
                            },
                        },
                    )
            return answer_result(item.questions, await manager.inbox.wait(item.id))

        async def tool_requester(args: dict, tool_call_id=None) -> dict:
            """Park a TOOL_REQUESTED prompt, then install the PINNED build if approved.

            Declining is a first-class outcome: the agent is told to fall back and disclose
            the gap rather than drop the check (OPE-85). Installs only ever come from the
            pinned registry with its digest verified — an approval is consent to install
            THAT artifact, not licence to fetch whatever a prompt asked for.
            """
            name = str(args.get("name", "")).strip()
            info = toolchain.describe(name)
            if not info:
                # Not in the pinned catalog: never show an install card that can only
                # end in "no pinned build" AFTER approval (owner-hit 2026-08-20 — agents
                # routed ordinary brew/pip installs through the card). Steer to the
                # shell, which has its own approval flow.
                return {
                    "installed": False,
                    "error": (
                        f"'{name}' is not in the pinned tool catalog "
                        f"({', '.join(sorted(toolchain.MANAGED))}). Install it yourself "
                        "with the shell (brew/pip/…, subject to the normal command "
                        "approval), or proceed without it and note the gap."
                    ),
                }
            item = manager.inbox.add_tool_request(
                session_id,
                f"Install {name}?" if name else "Install a tool?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "tool": name,
                    "installable": bool(info),
                    "version": (info or {}).get("version", ""),
                    "summary": (info or {}).get("summary", ""),
                    "url": (info or {}).get("url", ""),
                    "source": (info or {}).get("source", ""),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))  # {approved}
            if not resp.get("approved"):
                return {
                    "installed": False,
                    "reason": "the user declined to install it",
                }
            if not info:
                return {
                    "installed": False,
                    "error": f"no pinned build of {name} is available for this platform",
                }
            try:
                path = await asyncio.to_thread(toolchain.install, name)
            except Exception as exc:  # noqa: BLE001 - surfaced to the agent verbatim
                return {"installed": False, "error": str(exc)}
            return {"installed": True, "path": path, "version": info["version"]}

        async def directory_requester(args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted DIRECTORY_REQUESTED. Park, await, then apply the grant.
            item = manager.inbox.add_directory(
                session_id,
                "Grant access to a folder?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                    "primary": bool(args.get("primary", False)),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {granted, path, writable}
            if not resp.get("granted"):
                return {"granted": False, "reason": "the user declined the request"}
            path = (resp.get("path") or args.get("path") or "").strip()
            if not path:
                return {"granted": False, "error": "no directory was provided"}
            writable = bool(resp.get("writable", args.get("writable", False)))
            if bool(args.get("primary", False)):
                # Root promotion (workspace-scratch-design.md §5) — the shell cd inside
                # is blocking, keep it off the event loop.
                promo = await asyncio.to_thread(
                    manager.promote_workspace, session_id, path
                )
                if promo.get("ok"):
                    return {
                        "granted": True,
                        "path": promo["path"],
                        "writable": True,
                        "primary": True,
                        "note": (
                            "This folder is now the session's workspace. For the rest "
                            "of this turn, address it by absolute path."
                        ),
                    }
                # Promotion refused (e.g. the session already has a workspace): still
                # honor the grant as a plain additional folder.
                res = manager.add_root(session_id, path, writable)
                if not res.get("ok"):
                    return {
                        "granted": False,
                        "error": promo.get("error", "could not promote"),
                    }
                return {
                    "granted": True,
                    "path": path,
                    "writable": writable,
                    "primary": False,
                    "note": promo.get("error", "")
                    + " — granted as an additional folder instead",
                }
            res = manager.add_root(session_id, path, writable)
            if not res.get("ok"):
                return {
                    "granted": False,
                    "error": res.get("error", "could not grant access"),
                }
            primary = next(
                (
                    r
                    for r in res.get("roots", [])
                    if r.get("path")
                    and Path(r["path"]).expanduser().resolve()
                    == Path(path).expanduser().resolve()
                ),
                None,
            )
            return {
                "granted": True,
                "path": (primary or {}).get("path", path),
                "writable": writable,
            }

        async def plan_approver(_args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted PLAN_PROPOSED. Park, await the verdict.
            item = manager.inbox.add_plan(
                session_id,
                "Approve the plan?",
                body=str(_args.get("plan", "")),
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {approved, mode, feedback}
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user rejected the plan",
                }
            return {"approved": True, "mode": resp.get("mode") or "interactive"}

        async def team_approver(_args: dict, tool_call_id=None) -> dict:
            # The staffing gate. The engine already emitted TEAM_PROPOSED; park an
            # Inbox item as the durable resolution vehicle, wait for the verdict, and
            # on approval PRE-SPAWN the team (create_team fails closed on non-worker
            # personas, so a bad roster reads as a rejection with the reason).
            members = _args.get("members") or []
            roster = "\n".join(
                f"- {m.get('persona', '?')}"
                + (f" · {m['model']}" if m.get("model") else "")
                + (f" — {m['reason']}" if m.get("reason") else "")
                for m in members
                if isinstance(m, dict)
            )
            item = manager.inbox.add_plan(
                session_id,
                "Create this team?",
                body=roster,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user declined this roster",
                }
            # The gate checkbox is the USER's call: an explicit enable_chat in the
            # response overrides whatever the lead proposed.
            enable_chat = bool(
                resp["enable_chat"]
                if "enable_chat" in resp
                else _args.get("enable_chat", False)
            )
            return manager.create_team(
                session_id,
                [m for m in members if isinstance(m, dict)],
                enable_chat=enable_chat,
            )

        async def items_approver(_args: dict, tool_call_id=None) -> dict:
            # The decomposition gate. TEAMS-flavored sibling of plan_approver:
            # park a durable Inbox item, wait, and on approval create the items.
            items = _args.get("items") or []
            body = "\n".join(
                f"- {i.get('title', '?')} — Done when: {i.get('criteria', '?')}"
                for i in items
                if isinstance(i, dict)
            )
            item = manager.inbox.add_plan(
                session_id,
                "Approve the proposed work items?",
                body=body,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user declined the split",
                }
            return manager.board_create_items(
                session_id, [i for i in items if isinstance(i, dict)]
            )

        async def _apply_model(model: Optional[str]) -> None:
            # Mid-session rebind is allowed (roadmap item 3, supersedes the 2026-07-04
            # lock): history is canonical and providers convert per call. A real switch
            # appends a persisted notice; broadcast it so live views render the marker
            # and update their header. Never rebind mid-turn — the running loop reads
            # `engine.model` per iteration and a mixed turn is exactly the breakage the
            # old lock existed to prevent.
            if not model or manager.is_running(session_id):
                return
            notice = engine.switch_model(model)
            if notice is None:  # same model, or first bind on a fresh session
                return
            manager.persist_session(session_id)
            await manager.broadcast_session(
                session_id,
                {"type": "model_changed", "data": {"model": model, "text": notice}},
            )

        def _resolve_pending(resolution: str) -> None:
            # Live WS responses resolve THE session's single pending prompt (one at a time, since the
            # agent blocks). Reconnect / Inbox resolve by id via REST instead.
            pend = manager.inbox.pending(session_id)
            if pend:
                manager.inbox.resolve(pend[0].id, resolution)

        workspace = ws.query_params.get("workspace")
        mcp_tools = await manager.prepare_mcp_tools(
            session_id, workspace=workspace, agent=agent
        )
        engine = manager.get_engine(
            session_id,
            workspace=workspace,
            agent=agent,
            approver=approver,
            extra_tools=mcp_tools,
            directory_requester=directory_requester,
            plan_approver=plan_approver,
            question_asker=question_asker,
            tool_requester=tool_requester,
            team_approver=team_approver,
            items_approver=items_approver,
        )
        if engine is None:
            await ws.send_json(
                {
                    "type": "error",
                    "data": {
                        "error": "no valid workspace — choose a project folder first"
                    },
                }
            )
            await ws.close()
            return
        # MCP servers that failed to start while preparing this session's tools:
        # leave a quiet, persistent notice instead of the session silently lacking
        # them (drill 2026-08-20: three silent startup failures in a row).
        for name, err in manager.pop_mcp_failures(session_id):
            detail = f": {err}" if err else ""
            # `server` makes the notice structured: the GUI renders one quiet line
            # with the full error behind a disclosure + an Open-Connectors action
            # (owner ruling 2026-08-21) instead of a wall of stderr.
            engine._append_notice(
                "mcp_error",
                f"MCP server “{name}” failed to start{detail}"[:500],
                server=name,
            )
        # Auto-compaction failure prompt (OPE-27): only an ATTENDED session may be asked
        # Retry/Trim — unattended runs auto-trim (the policy in engine._compact_now).
        engine.is_attended = lambda: _visibility() == VIS_INLINE
        await ws.send_json(
            {
                "type": "ready",
                "data": {
                    "session_id": session_id,
                    # A reconnect can land MID-TURN (sidebar revisit, app relaunch, WS
                    # drop). Without server truth the GUI never learns a turn is live —
                    # no Stop button, no waiting row (owner catch 2026-08-24).
                    "running": manager.is_running(session_id),
                    "agent": getattr(engine, "agent_name", "code"),
                    "model": engine.model,
                    "mode": engine.permissions.mode.value,
                    "workspace": (
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    # UX-029: the GUI never shows a temporary folder's raw path — this flag
                    # is how it knows to say "Temporary folder" (and offer Save as project).
                    "temp_workspace": manager.is_temp_workspace(
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    "command_trust": manager.workspace_command_trust(
                        str(getattr(engine, "audit_context", {}).get("workspace", ""))
                    ),
                },
            }
        )

        # Checkpoint events: persist mid-turn so a crash/quit can't eat the conversation.
        # turn_start = the user message just landed (a brand-new session gets its row here,
        # not at connect — empty never-used sessions shouldn't appear in Recents);
        # permission_required/directory_requested = parked indefinitely on the user;
        # iteration_end = a model response + its tool results completed.
        _CHECKPOINTS = {
            "turn_start",
            "permission_required",
            "directory_requested",
            "plan_proposed",
            "iteration_end",
        }

        async def run_turn(content, *, retry: bool = False, display=None) -> None:
            # The receive loop atomically claims this session before scheduling the task.
            # Keeping the claim outside prevents two back-to-back frames from both starting.
            try:
                events = (
                    engine.retry()
                    if retry
                    else engine.run(content, display=display)
                )
                async for event in events:
                    # Broadcast to every socket viewing this session (this socket included — it's a
                    # registered client), so a second view of the same session stays in sync too.
                    await manager.broadcast_session(
                        session_id, {"type": event.type.value, "data": event.data}
                    )
                    if event.type.value in _CHECKPOINTS:
                        manager.save(session_id, engine)
                    if event.type.value == "turn_start":
                        # Title on the user's words the moment they land — never behind
                        # a long agentic turn (owner catch 2026-08-24).
                        manager._maybe_autotitle(session_id)
            finally:
                manager.mark_idle(session_id)
                manager.save(session_id, engine)
                await manager.broadcast_session(
                    session_id, {"type": "turn_done", "data": {}}
                )

        # This socket is now a live view of the session; background turns (channel delivery,
        # self-wake, durable resume) broadcast here too, not just locally driven run_turns.
        manager.register_session_client(session_id, ws.send_json)
        if engine.permissions.mode is Mode.AUTO_APPROVE and not any(
            m.get("kind") == "mode_notice" for m in engine.messages
        ):
            from coworker.permissions import AUTO_APPROVE_NOTICE

            engine._append_notice(
                "mode_notice", AUTO_APPROVE_NOTICE, title="Auto-approve is on."
            )
            manager.save(session_id, engine, touch=False)  # migration ≠ activity
            await ws.send_json(
                {
                    "type": "mode_notice",
                    "data": {
                        "title": "Auto-approve is on.",
                        "text": AUTO_APPROVE_NOTICE,
                    },
                }
            )
        inbound_times: deque[float] = deque()

        async def reject_input(reason: str) -> None:
            # Input validation failures are not provider failures and must not offer "Retry"
            # or flush an in-progress assistant stream in the GUI.
            await ws.send_json({"type": "input_rejected", "data": {"error": reason}})

        async def claim_turn(*, retry: bool = False, content=None, display=None) -> None:
            if not manager.try_mark_running(session_id):
                await reject_input(
                    "This session is already running a turn. Wait for it to finish or stop it."
                )
                return
            asyncio.create_task(run_turn(content, retry=retry, display=display))

        try:
            while True:
                try:
                    message = await ws.receive_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await reject_input("Invalid WebSocket message: expected JSON.")
                    continue

                now = asyncio.get_running_loop().time()
                while (
                    inbound_times
                    and now - inbound_times[0] > _WS_RATE_LIMIT_WINDOW_SECONDS
                ):
                    inbound_times.popleft()
                if len(inbound_times) >= _WS_RATE_LIMIT_COUNT:
                    await reject_input("Too many WebSocket messages; reconnect and try again.")
                    await ws.close(code=1008)
                    return
                inbound_times.append(now)

                if not isinstance(message, dict):
                    await reject_input("Invalid WebSocket message: expected an object.")
                    continue
                kind = message.get("type")
                if not isinstance(kind, str):
                    await reject_input("Invalid WebSocket message: missing string type.")
                    continue
                if kind == "approval":
                    _resolve_pending(message.get("decision", "deny"))
                elif kind == "directory_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "granted": bool(message.get("granted")),
                                "path": message.get("path", ""),
                                "writable": bool(message.get("writable", False)),
                            }
                        )
                    )
                elif kind == "tool_response":
                    _resolve_pending(
                        json.dumps({"approved": bool(message.get("approved"))})
                    )
                elif kind == "plan_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "mode": message.get("mode", "interactive"),
                                "feedback": message.get("feedback", ""),
                            }
                        )
                    )
                elif kind in ("team_response", "items_response"):
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "feedback": message.get("feedback", ""),
                                **(
                                    {"enable_chat": bool(message.get("enable_chat"))}
                                    if "enable_chat" in message
                                    else {}
                                ),
                            }
                        )
                    )
                elif kind == "question_response":
                    _resolve_pending(str(message.get("answer", "")))
                elif kind == "allow_anyway":
                    # §8.4: the user clicked "Allow anyway" on a reviewer-denied tool card.
                    # Registers a ONE-SHOT exact-action approval on the engine; the GUI then
                    # sends its canned retry message through the normal user_message path,
                    # and the re-proposed identical action runs without the reviewer/card.
                    name = message.get("name")
                    arguments = message.get("arguments")
                    if not isinstance(name, str) or not name:
                        await reject_input("Invalid allow_anyway: missing tool name.")
                    elif arguments is not None and not isinstance(arguments, dict):
                        await reject_input("Invalid allow_anyway: arguments must be an object.")
                    else:
                        engine.approve_action_once(name, arguments or {})
                elif kind == "interrupt":
                    engine.request_interrupt()
                elif kind == "retry":
                    # Re-run after a provider error (engine guards on the error-notice
                    # tail, so a stray frame is a no-op that still ends with turn_done).
                    await claim_turn(retry=True)
                elif kind == "set_mode":
                    try:
                        new_mode = Mode(message.get("mode"))
                    except (TypeError, ValueError):
                        pass
                    else:
                        previous = engine.permissions.mode
                        engine.permissions.mode = new_mode
                        if previous is not new_mode:
                            manager.audit_autonomy_change(
                                session_id, "mode", previous.value, new_mode.value
                            )
                            # The transcript records which mode each exchange ran under
                            # (owner ruling 2026-08-24): full explainer the first time a
                            # session enters Auto-Approve, a one-line marker otherwise.
                            # Server-authored + persisted, so reloads show it in place
                            # exactly once instead of re-announcing on every restart.
                            from coworker.permissions import (
                                AUTO_APPROVE_NOTICE,
                                MODE_LABELS,
                            )

                            if new_mode is Mode.AUTO_APPROVE and not any(
                                m.get("kind") == "mode_notice"
                                for m in engine.messages
                            ):
                                engine._append_notice(
                                    "mode_notice",
                                    AUTO_APPROVE_NOTICE,
                                    title="Auto-approve is on.",
                                )
                                notice_data = {
                                    "title": "Auto-approve is on.",
                                    "text": AUTO_APPROVE_NOTICE,
                                }
                            else:
                                label = MODE_LABELS.get(
                                    new_mode.value, new_mode.value
                                )
                                engine._append_notice(
                                    "mode_switch", f"{label} is on."
                                )
                                notice_data = {"text": f"{label} is on."}
                            # A mode switch with no accompanying message is bookkeeping,
                            # not activity (owner ruling 2026-08-24): the transcript
                            # records it, Recents doesn't reorder. The next real turn's
                            # checkpoint save bumps recency as usual.
                            manager.save(session_id, engine, touch=False)
                            await manager.broadcast_session(
                                session_id,
                                {"type": "mode_notice", "data": notice_data},
                            )
                elif kind == "set_model":
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                    else:
                        await _apply_model(model)
                elif kind == "user_message":
                    raw_text = message.get("text")
                    if raw_text is None:
                        raw_text = ""
                    if not isinstance(raw_text, str):
                        await reject_input("Invalid message text: expected a string.")
                        continue
                    text = raw_text.strip()
                    raw_attachments = message.get("attachments")
                    attachments = [] if raw_attachments is None else raw_attachments
                    # Reject an oversized frame instead of buffering it into a turn. Send a
                    # visible error so the surface can tell the user, and drop the message.
                    if not isinstance(attachments, list):
                        await reject_input("Invalid attachments: expected a list.")
                        continue
                    reject = None
                    if len(text) > _MAX_MESSAGE_TEXT_CHARS:
                        reject = (
                            f"Message too long ({len(text)} chars; "
                            f"limit {_MAX_MESSAGE_TEXT_CHARS})."
                        )
                    elif len(attachments) > _MAX_ATTACHMENTS:
                        reject = (
                            f"Too many attachments ({len(attachments)}; "
                            f"limit {_MAX_ATTACHMENTS})."
                        )
                    elif any(not isinstance(a, dict) for a in attachments):
                        reject = "Invalid attachment: expected an object."
                    elif _json_value_size(attachments) > _MAX_ATTACHMENTS_BYTES:
                        reject = "Attachments too large (limit 15 MB per message)."
                    else:
                        for attachment in attachments:
                            attachment_kind = attachment.get("kind")
                            name = attachment.get("name")
                            mime = attachment.get("mime")
                            if attachment_kind not in {"image", "pdf", "text"}:
                                reject = "Invalid attachment kind."
                            elif name is not None and (
                                not isinstance(name, str) or len(name) > 1024
                            ):
                                reject = "Invalid attachment name."
                            elif mime is not None and (
                                not isinstance(mime, str) or len(mime) > 255
                            ):
                                reject = "Invalid attachment MIME type."
                            elif attachment_kind == "image":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith("data:image/")
                                    or ";base64," not in data
                                    or len(data) > MAX_IMAGE_CHARS
                                ):
                                    reject = "Invalid or oversized image attachment."
                            elif attachment_kind == "pdf":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith(
                                        "data:application/pdf;base64,"
                                    )
                                    or len(data) > MAX_PDF_CHARS
                                ):
                                    reject = "Invalid or oversized PDF attachment."
                            else:
                                body = attachment.get("text")
                                if (
                                    not isinstance(body, str)
                                    or len(body) > MAX_TEXT_CHARS
                                ):
                                    reject = "Invalid or oversized text attachment."
                            if reject is not None:
                                break
                    if reject is not None:
                        await reject_input(reject)
                        continue
                    # The composer sends its visible model with every message — the FIRST
                    # one binds the session (race-proof across reconnects; see api.ts
                    # Session.userMessage), later ones may switch it (notice persisted).
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                        continue
                    # Force-run (SKILLS-SPEC §4.1 #3): the composer's `/skill` pick rides as a
                    # separate field. Validated against the session's effective menu — a muted
                    # or unknown skill is a visible error, never a silent no-op (§4.6 #15).
                    # The model-facing framing goes into `content`; the transcript shows the
                    # user's literal "/name …" line via the `_display` sidecar (one bubble).
                    skill = message.get("skill")
                    display = None
                    if skill is not None:
                        if not isinstance(skill, str) or not skill.strip():
                            await reject_input("Invalid skill: expected a name.")
                            continue
                        skill = skill.strip()
                        menu = manager.effective_skill_names(session_id, workspace)
                        if skill not in menu:
                            await reject_input(
                                f"Skill '{skill}' is not available in this session."
                            )
                            continue
                        display = f"/{skill}" + (f" {text}" if text else "")
                        text = (
                            f'Use the skill "{skill}" for this request: first call '
                            f'load_skill("{skill}") and follow its instructions.'
                            + (f"\n\n{text}" if text else "")
                        )
                    await _apply_model(model)
                    if text or attachments:
                        content = build_user_content(text, attachments)
                        await claim_turn(content=content, display=display)
                else:
                    await reject_input(f"Unknown WebSocket message type: {kind}.")
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_session_client(session_id, ws.send_json)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """App-wide event stream (session-independent): the GUI keeps one open for
        pushes like automation_run_started (the UX-026 toast). Read-only — inbound
        frames are ignored; the receive loop just detects disconnect."""
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol=_websocket_protocol(ws))
        manager.register_event_client(ws.send_json)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_event_client(ws.send_json)

    @app.websocket("/ws/team")
    async def ws_team(ws: WebSocket) -> None:
        """Durable Team stream: cursor catch-up followed by live notifications."""
        if not _websocket_authenticated(ws) or not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        try:
            after_sequence = int(ws.query_params.get("after_sequence", "0"))
        except ValueError:
            await ws.close(code=1008)
            return
        if after_sequence < 0:
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol=_websocket_protocol(ws))
        manager.register_team_event_client(ws.send_json)
        try:
            catchup = await asyncio.to_thread(
                manager.team_service.list_events,
                after_sequence=after_sequence,
                limit=200,
            )
            await ws.send_json({"type": "team_catchup", "data": catchup})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_team_event_client(ws.send_json)

    # Static file hosting for Web UI (SPA) when built dist exists
    dist_dir = Path(__file__).resolve().parent.parent.parent / "surfaces" / "gui" / "dist"
    if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static_gui")

    return app


def _parse_json(s: str) -> dict[str, Any]:
    """Parse a structured Inbox resolution (directory/plan carry their reply as a JSON string)."""
    try:
        v = json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _openai_response(model: str, turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": turn.finish_reason or "stop",
            }
        ],
    }
