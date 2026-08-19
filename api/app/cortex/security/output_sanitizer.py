"""Output sanitization helpers for Cortex responses."""

from typing import Any, Dict, List, Tuple

from app.cortex.security.credential_patterns import (
    ALLOWLIST_PATTERNS,
    ASSIGNMENT_SECRET_VALUE_PATTERN,
    CREDENTIAL_PATTERNS,
    JSON_SECRET_VALUE_PATTERN,
)


def _is_allowlisted(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    return any(pattern.fullmatch(candidate) for pattern in ALLOWLIST_PATTERNS)


def _next_placeholder(kind: str, index: int) -> str:
    return f"[REDACTED:{kind}:{index}]"


def _is_placeholder(value: str) -> bool:
    candidate = str(value or "").strip()
    return candidate.startswith("[REDACTED:") and candidate.endswith("]")


def sanitize_model_output(text: str) -> Dict[str, Any]:
    """Return sanitized output payload and metadata."""
    raw_text = str(text or "")
    if not raw_text:
        return {
            "text": "",
            "redaction_count": 0,
            "restored_count": 0,
            "detected_types": [],
            "suspicious": False,
            "suspicious_reasons": [],
        }

    sanitized = raw_text
    replacements: List[Tuple[str, str, str]] = []  # (placeholder, original, kind)
    detected_types: List[str] = []
    index = 0

    def _record(kind: str, original: str) -> str:
        nonlocal index
        index += 1
        placeholder = _next_placeholder(kind, index)
        replacements.append((placeholder, original, kind))
        detected_types.append(kind)
        return placeholder

    for kind, pattern in CREDENTIAL_PATTERNS:
        def _replace_match(match: Any) -> str:
            return _record(kind, match.group(0))
        sanitized = pattern.sub(_replace_match, sanitized)

    def _replace_json_value(match: Any) -> str:
        prefix = match.group(1)
        secret_value = match.group(2)
        suffix = match.group(3)
        if _is_placeholder(secret_value):
            return match.group(0)
        return f"{prefix}{_record('json_secret_value', secret_value)}{suffix}"

    sanitized = JSON_SECRET_VALUE_PATTERN.sub(_replace_json_value, sanitized)

    def _replace_assignment_value(match: Any) -> str:
        prefix = match.group(1)
        secret_value = match.group(2)
        if _is_placeholder(secret_value):
            return match.group(0)
        return f"{prefix}{_record('assignment_secret_value', secret_value)}"

    sanitized = ASSIGNMENT_SECRET_VALUE_PATTERN.sub(_replace_assignment_value, sanitized)

    restored_count = 0
    kept_types: List[str] = []
    for placeholder, original, kind in replacements:
        if _is_allowlisted(original):
            sanitized = sanitized.replace(placeholder, original)
            restored_count += 1
        else:
            kept_types.append(kind)

    unique_kept_types = sorted(set(kept_types))
    redaction_count = max(0, len(replacements) - restored_count)

    suspicious_reasons: List[str] = []
    if redaction_count >= 3:
        suspicious_reasons.append("multiple_redactions")
    if "private_key_block" in unique_kept_types:
        suspicious_reasons.append("private_key_detected")
    if "json_secret_value" in unique_kept_types or "assignment_secret_value" in unique_kept_types:
        suspicious_reasons.append("credential_field_leak")
    if len(raw_text) > 0:
        redaction_ratio = redaction_count / max(1, len(raw_text.split()))
        if redaction_ratio >= 0.15:
            suspicious_reasons.append("high_redaction_density")

    return {
        "text": sanitized,
        "redaction_count": redaction_count,
        "restored_count": restored_count,
        "detected_types": unique_kept_types,
        "suspicious": bool(suspicious_reasons),
        "suspicious_reasons": suspicious_reasons,
    }
