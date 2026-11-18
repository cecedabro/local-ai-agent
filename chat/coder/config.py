"""Settings for the coder chat app (SPEC §1).

Reads from the `settings` dict first, then `os.environ`, then defaults.
Booleans: ``"0"``, ``0``, ``""``, ``"false"`` are false.
"""
from __future__ import annotations
import os


def _bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v)
    return s not in ("0", "", "false")


class Settings:
    __slots__ = (
        "DATA_DIR", "GATEWAY_URL", "GATEWAY_KEY", "STATUS_URL",
        "CTX_DEFAULT", "ADMIN_USER", "ADMIN_PASSWORD", "SECURE_COOKIES",
        "MODEL_LABEL", "HARDWARE_LABEL", "REPO_URL", "WAIT_LIMIT",
    )

    def __init__(self, settings: dict | None = None) -> None:
        raw = settings or {}
        env = os.environ

        self.DATA_DIR = str(raw.get("DATA_DIR", env.get("DATA_DIR", "./data")))
        self.GATEWAY_URL = str(raw.get("GATEWAY_URL", env.get("GATEWAY_URL", "http://agent-status.ai-agent.svc:8080/v1")))
        self.GATEWAY_KEY = str(raw.get("GATEWAY_KEY", env.get("GATEWAY_KEY", "")))
        self.STATUS_URL = str(raw.get("STATUS_URL", env.get("STATUS_URL", "")))
        self.CTX_DEFAULT = int(raw.get("CTX_DEFAULT", env.get("CTX_DEFAULT", "32768")))
        self.ADMIN_USER = str(raw.get("ADMIN_USER", env.get("ADMIN_USER", "")))
        self.ADMIN_PASSWORD = str(raw.get("ADMIN_PASSWORD", env.get("ADMIN_PASSWORD", "")))
        self.SECURE_COOKIES = _bool(raw.get("SECURE_COOKIES", env.get("SECURE_COOKIES", "1")))
        self.MODEL_LABEL = str(raw.get("MODEL_LABEL", env.get("MODEL_LABEL", "Qwen3.6 35B-A3B · 4-bit")))
        self.HARDWARE_LABEL = str(raw.get("HARDWARE_LABEL", env.get("HARDWARE_LABEL", "running locally on an RTX 3080 Ti")))
        self.REPO_URL = str(raw.get("REPO_URL", env.get("REPO_URL", "https://github.com/cecedabro/local-ai-agent")))
        self.WAIT_LIMIT = int(raw.get("WAIT_LIMIT", env.get("WAIT_LIMIT", "120")))
