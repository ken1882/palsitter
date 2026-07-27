from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

import psutil

from module.instances import config_dir


DEFAULT_BIND_ADDRESS = "127.0.0.1"
PASSWORD_ITERATIONS = 310_000


def settings_path() -> Path:
    return config_dir() / "webui" / "settings.json"


@dataclass(frozen=True)
class WebUISettings:
    bind_address: str = DEFAULT_BIND_ADDRESS
    auto_update: bool = False
    auth_enabled: bool = False
    auth_username: str = ""
    auth_salt: str = ""
    auth_password_hash: str = ""

    @property
    def has_password(self) -> bool:
        return bool(self.auth_salt and self.auth_password_hash)

    def to_dict(self) -> dict[str, object]:
        return {
            "bind_address": self.bind_address,
            "auto_update": self.auto_update,
            "web_auth": {
                "enabled": self.auth_enabled,
                "username": self.auth_username,
                "salt": self.auth_salt,
                "password_hash": self.auth_password_hash,
            },
        }


def _read_data() -> dict[str, object]:
    try:
        value = json.loads(settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_web_settings() -> WebUISettings:
    data = _read_data()
    auth = data.get("web_auth")
    auth = auth if isinstance(auth, dict) else {}
    address = str(data.get("bind_address") or DEFAULT_BIND_ADDRESS)
    try:
        address = str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError:
        address = DEFAULT_BIND_ADDRESS
    return WebUISettings(
        bind_address=address,
        auto_update=bool(data.get("auto_update", False)),
        auth_enabled=bool(auth.get("enabled", False)),
        auth_username=str(auth.get("username") or ""),
        auth_salt=str(auth.get("salt") or ""),
        auth_password_hash=str(auth.get("password_hash") or ""),
    )


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def save_web_settings(settings: WebUISettings) -> None:
    data = _read_data()
    data.update(settings.to_dict())
    _atomic_write(settings_path(), data)


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, salt: str, expected: str) -> bool:
    try:
        salt_bytes = base64.urlsafe_b64decode(salt.encode("ascii"))
        expected_bytes = base64.urlsafe_b64decode(expected.encode("ascii"))
    except (ValueError, UnicodeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PASSWORD_ITERATIONS
    )
    return hmac.compare_digest(actual, expected_bytes)


def is_localhost(address: str) -> bool:
    return str(address) == DEFAULT_BIND_ADDRESS


def interface_options(saved_address: str | None = None) -> list[dict[str, str]]:
    addresses: list[tuple[str, str]] = []
    try:
        interfaces = psutil.net_if_addrs()
    except OSError:
        interfaces = {}
    for interface, values in sorted(interfaces.items(), key=lambda item: item[0].casefold()):
        for value in values:
            if getattr(value, "family", None) != socket.AF_INET:
                continue
            address = str(getattr(value, "address", ""))
            try:
                address = str(ipaddress.IPv4Address(address))
            except ipaddress.AddressValueError:
                continue
            if address not in {item[1] for item in addresses}:
                addresses.append((interface, address))
    options = [
        {"label": "localhost — 127.0.0.1", "value": DEFAULT_BIND_ADDRESS},
        {"label": "All interfaces — 0.0.0.0", "value": "0.0.0.0"},
    ]
    for interface, address in addresses:
        if address in {DEFAULT_BIND_ADDRESS, "0.0.0.0"}:
            continue
        options.append({"label": f"{interface} — {address}", "value": address})
    if saved_address and saved_address not in {item["value"] for item in options}:
        options.append({"label": f"Saved address — {saved_address}", "value": saved_address})
    return options


def resolve_bind_address(explicit: str | None = None) -> str:
    value = explicit or os.getenv("PALSITTER_HOST") or load_web_settings().bind_address
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"Invalid IPv4 bind address: {value}") from exc


__all__ = [
    "DEFAULT_BIND_ADDRESS",
    "WebUISettings",
    "hash_password",
    "interface_options",
    "is_localhost",
    "load_web_settings",
    "resolve_bind_address",
    "save_web_settings",
    "settings_path",
    "verify_password",
]
