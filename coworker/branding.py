"""Canonical product identity for Astra.

The Python package remains ``coworker`` for import and upgrade compatibility. These
constants are for product-facing boundaries only: commands, transport identifiers,
and product identity.
"""

from __future__ import annotations

import os
from typing import Optional

PRODUCT_NAME = "Astra"
PRODUCT_SLUG = "astra"

ASTRA_ENV_PREFIX = "ASTRA_"
ASTRA_AUTH_HEADER = "X-Astra-Token"
ASTRA_WS_SUBPROTOCOL = "astra"


def legacy_env_name(name: str) -> Optional[str]:
    return None


def getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an Astra environment variable."""
    return os.environ.get(name, default)


def env_flag(name: str, *, default: bool = False) -> bool:
    """Parse a boolean Astra variable."""
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")

