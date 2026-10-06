"""Small shared helpers: unique ids and ISO timestamps."""

from __future__ import annotations

import datetime
import secrets


def new_id(prefix: str) -> str:
    """Return a short, collision-resistant id such as ``run_3f9ac21d``."""
    return f"{prefix}_{secrets.token_hex(4)}"


def now_iso() -> str:
    """Local wall-clock time as an ISO-8601 string (second precision)."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
