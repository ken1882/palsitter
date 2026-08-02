from __future__ import annotations

import base64
import binascii
import datetime as dt
import hmac
from dataclasses import dataclass
from typing import Any

from module.webui.global_audit import GlobalAuditEvent, GlobalAuditStore
from module.webui.settings import WebUISettings, verify_password


DESKTOP_SESSION_COOKIE = "palsitter_desktop_session"


@dataclass(frozen=True)
class AuthResult:
    allowed: bool
    username: str = ""
    method: str = "basic"


class WebAuth:
    def __init__(self, settings: WebUISettings, desktop_token: str = "") -> None:
        self.settings = settings
        self.desktop_token = desktop_token
        self.audit = GlobalAuditStore()

    def _audit(self, request: Any, result: AuthResult) -> None:
        if not self.settings.auth_enabled:
            return
        source_ip = str(getattr(request, "remote_ip", "") or "")
        event_type = "web_login_success" if result.allowed else "web_login_failure"
        message = "Control-panel authentication succeeded" if result.allowed else "Control-panel authentication failed"
        try:
            self.audit.append(
                GlobalAuditEvent(
                    dt.datetime.now(dt.timezone.utc),
                    event_type,
                    result.username,
                    source_ip,
                    result.method,
                    message,
                )
            )
        except (OSError, ValueError):
            # Authentication must not fail because an audit file is unavailable.
            pass

    def authorize(self, request: Any) -> AuthResult:
        supplied_token = str(request.headers.get("X-Palsitter-Desktop-Token", ""))
        if not supplied_token:
            cookie = getattr(request, "cookies", {}).get(DESKTOP_SESSION_COOKIE)
            supplied_token = str(getattr(cookie, "value", cookie) or "")
        if self.desktop_token and hmac.compare_digest(supplied_token, self.desktop_token):
            result = AuthResult(True, "desktop", "desktop_token")
            self._audit(request, result)
            return result
        if not self.settings.auth_enabled:
            return AuthResult(True, method="none")

        header = str(request.headers.get("Authorization", ""))
        username = ""
        if header.casefold().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (binascii.Error, UnicodeDecodeError, ValueError):
                password = ""
            allowed = (
                hmac.compare_digest(username, self.settings.auth_username)
                and self.settings.has_password
                and verify_password(password, self.settings.auth_salt, self.settings.auth_password_hash)
            )
            result = AuthResult(allowed, username, "basic")
        else:
            result = AuthResult(False, "", "basic")
        self._audit(request, result)
        return result


__all__ = ["AuthResult", "DESKTOP_SESSION_COOKIE", "WebAuth"]
