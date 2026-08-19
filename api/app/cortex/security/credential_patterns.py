"""High-confidence credential/secret detection patterns for output sanitization."""

import re
from typing import Pattern, Tuple


CREDENTIAL_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("fernet_ciphertext", re.compile(r"\bgAAAAA[A-Za-z0-9_\-]{30,}\b")),
    ("bearer_jwt", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
    ),
)


JSON_SECRET_VALUE_PATTERN: Pattern[str] = re.compile(
    r'(?i)("(?:access_token|refresh_token|client_secret|api_key|token|password)"\s*:\s*")([^"\n]+)(")'
)


ASSIGNMENT_SECRET_VALUE_PATTERN: Pattern[str] = re.compile(
    r"(?im)^(\s*(?:export\s+)?(?:ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|API_KEY|SLACK_BOT_TOKEN|JIRA_CLIENT_SECRET|OPENAI_API_KEY|ANTHROPIC_API_KEY|PASSWORD)\s*=\s*)([^\s#]+)"
)


ALLOWLIST_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"\b[A-Z][A-Z0-9]{1,14}-\d{1,7}\b"),  # Jira issue key
    re.compile(r"\bproj-\d{4}-\d{4}\b"),  # ProMarshal project id
    re.compile(r"\b(?:U|C|D|T)[A-Z0-9]{8,12}\b"),  # Slack ids
)
