#!/usr/bin/env python
"""Classify a user message into router path: team_poll or cortex (no execution)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.cortex.ingress import classify_message_intent  # noqa: E402
from app.promarshal.scope_policy import classify_scope_for_message  # noqa: E402
from app.team_poll.orchestrator import (  # noqa: E402
    classify_owner_free_text_intent_with_confidence,
)


def _route_probe(
    *,
    text: str,
    is_dm: bool,
    member_role: str,
    active_team_poll_session: bool,
    active_cadence_session: bool,
    team_poll_enabled: bool,
    scope_policy_enabled: bool,
) -> Dict[str, Any]:
    normalized_text = str(text or "").strip()
    role = str(member_role or "").strip().lower()

    result: Dict[str, Any] = {
        "input": {
            "text": normalized_text,
            "is_dm": bool(is_dm),
            "member_role": role,
            "active_team_poll_session": bool(active_team_poll_session),
            "active_cadence_session": bool(active_cadence_session),
            "team_poll_enabled": bool(team_poll_enabled),
            "scope_policy_enabled": bool(scope_policy_enabled),
        }
    }

    # 1) Active session routing takes precedence.
    if active_cadence_session:
        result["route"] = "cadence_session"
        result["reason"] = "active_cadence_session"
        return result

    if active_team_poll_session:
        result["route"] = "team_poll_session"
        result["reason"] = "active_team_poll_session"
        return result

    # 2) Owner free-text poll trigger (DM only).
    if is_dm and team_poll_enabled and role == "owner":
        owner_intent = classify_owner_free_text_intent_with_confidence(normalized_text)
        result["owner_poll_intent"] = owner_intent
        intent = str(owner_intent.get("intent") or "none")
        confidence = float(owner_intent.get("confidence") or 0.0)
        if intent != "none" and confidence >= 0.7:
            result["route"] = "team_poll_owner_intent"
            result["reason"] = f"owner_intent_{intent}"
            return result
        if intent != "none" and confidence < 0.7:
            result["route"] = "team_poll_owner_clarification"
            result["reason"] = "low_confidence_owner_intent"
            return result

    # 3) Greeting short-circuit.
    message_intent = classify_message_intent(normalized_text)
    result["message_intent"] = message_intent
    if message_intent == "greeting_only":
        result["route"] = "greeting_only"
        result["reason"] = "classify_message_intent"
        return result

    # 4) Global scope policy gate.
    if scope_policy_enabled:
        scope = classify_scope_for_message(normalized_text)
        result["scope"] = {
            "classification": scope.classification,
            "reason_code": scope.reason_code,
        }
        if scope.classification == "out_of_scope":
            result["route"] = "scope_blocked"
            result["reason"] = scope.reason_code
            return result

    # 5) Default handoff.
    result["route"] = "cortex"
    result["reason"] = "default_handoff"
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe router classification path (poll vs cortex) without execution.",
    )
    parser.add_argument("text", help="User message text to classify.")
    parser.add_argument(
        "--member-role",
        default="member",
        choices=["owner", "member"],
        help="Role used for owner poll-intent branch.",
    )
    parser.add_argument(
        "--is-dm",
        action="store_true",
        default=True,
        help="Mark message as DM channel (default: True).",
    )
    parser.add_argument(
        "--not-dm",
        action="store_true",
        help="Mark message as non-DM channel.",
    )
    parser.add_argument(
        "--active-team-poll-session",
        action="store_true",
        help="Simulate an active team poll session for this user.",
    )
    parser.add_argument(
        "--active-cadence-session",
        action="store_true",
        help="Simulate an active cadence session for this user.",
    )
    parser.add_argument(
        "--disable-team-poll",
        action="store_true",
        help="Disable owner free-text poll trigger for this probe.",
    )
    parser.add_argument(
        "--disable-scope-policy",
        action="store_true",
        help="Disable scope gate for this probe.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    is_dm = bool(args.is_dm) and not bool(args.not_dm)
    team_poll_enabled = (
        not bool(args.disable_team_poll)
        and bool(getattr(settings, "team_poll_enabled", True))
    )
    scope_policy_enabled = (
        not bool(args.disable_scope_policy)
        and bool(getattr(settings, "promarshal_global_scope_policy_enabled", False))
    )

    result = _route_probe(
        text=args.text,
        is_dm=is_dm,
        member_role=args.member_role,
        active_team_poll_session=bool(args.active_team_poll_session),
        active_cadence_session=bool(args.active_cadence_session),
        team_poll_enabled=team_poll_enabled,
        scope_policy_enabled=scope_policy_enabled,
    )

    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
