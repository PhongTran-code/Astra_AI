"""Astra-owned Google OAuth for Gmail.

Consent returns an authorization code directly to Astra. Astra exchanges the
code and keeps its refresh token in the local SecretStore; no connector broker
or Astra service is involved.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as secure_random
import time
import urllib.parse
from typing import Any, Optional

import httpx

from .config import Config
from .secrets import SecretStore

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_PENDING_TTL = 600
_REFRESH_LEEWAY = 120
_GMAIL_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)

# One-use CSRF + PKCE state. Tokens never live in memory before the callback.
_pending: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def is_configured(config: Config) -> bool:
    return bool(
        str(config.google_oauth_client_id).strip()
        and str(config.google_oauth_client_secret).strip()
    )


def redirect_uri(config: Config) -> str:
    configured = str(config.google_oauth_redirect_uri or "").strip()
    if configured:
        return configured
    return f"http://{config.host}:{config.port}/oauth/google/callback"


def _discard_expired_states() -> None:
    for state, pending in list(_pending.items()):
        if float(pending.get("created") or 0) < _now() - _PENDING_TTL:
            _pending.pop(state, None)


def begin_gmail_connect(config: Config) -> dict[str, Any]:
    """Create a PKCE Google consent URL for one Gmail account."""
    if not is_configured(config):
        return {
            "ok": False,
            "error": "Google OAuth is not configured on this Astra server",
        }
    _discard_expired_states()
    verifier = _b64url(secure_random.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secure_random.token_urlsafe(32)
    _pending[state] = {"created": _now(), "verifier": verifier}
    params = {
        "client_id": config.google_oauth_client_id,
        "redirect_uri": redirect_uri(config),
        "response_type": "code",
        "scope": " ".join(_GMAIL_SCOPES),
        "state": state,
        "access_type": "offline",
        # Re-consent recovers a refresh token after a prior Astra grant.
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {
        "ok": True,
        "authorize_url": _AUTHORIZE_URL + "?" + urllib.parse.urlencode(params),
    }


def _consume_pending(state: str) -> Optional[dict[str, Any]]:
    _discard_expired_states()
    return _pending.pop(state, None)


def complete_gmail_connect(
    secrets: SecretStore, config: Config, *, code: str, state: str
) -> dict[str, Any]:
    """Exchange a one-time Google code and persist one Gmail account profile."""
    pending = _consume_pending(state)
    if pending is None:
        return {"ok": False, "error": "unknown or expired Google sign-in attempt"}
    if not code:
        return {"ok": False, "error": "missing authorization code"}
    if not is_configured(config):
        return {"ok": False, "error": "Google OAuth is not configured on this Astra server"}
    try:
        token_response = httpx.post(
            _TOKEN_URL,
            data={
                "client_id": config.google_oauth_client_id,
                "client_secret": config.google_oauth_client_secret,
                "code": code,
                "code_verifier": pending["verifier"],
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri(config),
            },
            timeout=20,
        )
    except httpx.HTTPError:
        return {"ok": False, "error": "could not reach Google to finish sign-in"}
    if token_response.status_code != 200:
        return {"ok": False, "error": "Google rejected the authorization code"}
    token = token_response.json()
    access_token = str(token.get("access_token") or "")
    if not access_token:
        return {"ok": False, "error": "Google did not return an access token"}
    try:
        identity_response = httpx.get(
            _USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except httpx.HTTPError:
        return {"ok": False, "error": "could not read the Google account identity"}
    if identity_response.status_code != 200:
        return {"ok": False, "error": "Google did not return an email address"}
    email = str(identity_response.json().get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "Google did not return an email address"}
    from .connectors import gmail_accounts

    existing = secrets.get(gmail_accounts.PREFIX + email) or {}
    refresh_token = str(token.get("refresh_token") or existing.get("refresh_token") or "")
    profile = {
        "type": "oauth",
        "enabled": True,
        "managed": False,
        "oauth_provider": "google",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "scope": str(token.get("scope") or " ".join(_GMAIL_SCOPES)),
        "expires": _now() + max(int(token.get("expires_in") or 3600) - 60, 0),
        "account": email,
    }
    result = gmail_accounts.managed_connect_account(secrets, profile)
    if result.get("ok"):
        result["account"] = email
    return result


def refresh_gmail_token(
    secrets: SecretStore, config: Config, *, profile_key: str, leeway: int = _REFRESH_LEEWAY
) -> Optional[dict[str, Any]]:
    """Refresh an Astra-owned Google profile in place when it is near expiry."""
    profile = secrets.get(profile_key) or {}
    if profile.get("oauth_provider") != "google":
        return None
    expires = float(profile.get("expires") or 0)
    if expires and expires > _now() + leeway:
        return profile
    refresh_token = str(profile.get("refresh_token") or "")
    if not refresh_token:
        profile["oauth_error"] = "Google sign-in expired"
        secrets.put(profile_key, profile)
        return None
    if not is_configured(config):
        profile["oauth_error"] = "Google OAuth is not configured"
        secrets.put(profile_key, profile)
        return None
    try:
        response = httpx.post(
            _TOKEN_URL,
            data={
                "client_id": config.google_oauth_client_id,
                "client_secret": config.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        profile["oauth_error"] = "Google sign-in expired"
        secrets.put(profile_key, profile)
        return None
    fresh = response.json()
    access_token = str(fresh.get("access_token") or "")
    if not access_token:
        profile["oauth_error"] = "Google sign-in expired"
        secrets.put(profile_key, profile)
        return None
    profile["access_token"] = access_token
    if fresh.get("refresh_token"):
        profile["refresh_token"] = str(fresh["refresh_token"])
    profile["scope"] = str(fresh.get("scope") or profile.get("scope") or "")
    profile["expires"] = _now() + max(int(fresh.get("expires_in") or 3600) - 60, 0)
    profile.pop("oauth_error", None)
    secrets.put(profile_key, profile)
    return profile
