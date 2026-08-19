"""Stage R1 deterministic text normalization helpers."""

from __future__ import annotations

from typing import Any


def normalize_text(text: Any) -> str:
    """Normalize user input for deterministic matching."""
    raw = str(text or "")
    return " ".join(raw.strip().split())

