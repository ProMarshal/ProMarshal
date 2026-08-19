import asyncio

from app.core.config import settings
from app.team_poll import question_rewriter
from app.team_poll.question_rewriter import resolve_poll_question


def test_resolve_poll_question_deterministic_when_rewrite_disabled():
    prev_enabled = settings.team_poll_question_rewrite_enabled
    try:
        settings.team_poll_question_rewrite_enabled = False
        result = asyncio.run(
            resolve_poll_question(
                owner_text="ask team if everyone is ready for release",
                parsed_question="Is everyone ready for release?",
                parsed_mode={"response_format": "free_text", "options": [], "deadline_mode": "default"},
                project_name="demo",
                project_timezone="UTC",
            )
        )
    finally:
        settings.team_poll_question_rewrite_enabled = prev_enabled

    assert result["question_text"] == "Is everyone ready for release?"
    assert result["source"] == "deterministic"
    assert result["confidence"] == 1.0


def test_resolve_poll_question_retries_then_uses_llm(monkeypatch):
    prev_enabled = settings.team_poll_question_rewrite_enabled
    prev_retries = settings.team_poll_question_rewrite_max_retries
    prev_backoff = settings.team_poll_question_rewrite_backoff_ms
    prev_jitter = settings.team_poll_question_rewrite_retry_jitter_ms
    prev_min_conf = settings.team_poll_question_rewrite_min_confidence
    attempts = {"count": 0}

    async def _fake_rewrite(**kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return None
        return {
            "question_text": "Did you submit your timesheet for 2026-03-02 to 2026-03-08?",
            "confidence": 0.93,
            "model": "unit-test-model",
        }

    async def _fake_sleep(_seconds):
        return None

    try:
        settings.team_poll_question_rewrite_enabled = True
        settings.team_poll_question_rewrite_max_retries = 2
        settings.team_poll_question_rewrite_backoff_ms = "1,1"
        settings.team_poll_question_rewrite_retry_jitter_ms = 0
        settings.team_poll_question_rewrite_min_confidence = 0.7
        monkeypatch.setattr(question_rewriter, "_rewrite_with_llm", _fake_rewrite)
        monkeypatch.setattr(question_rewriter.asyncio, "sleep", _fake_sleep)

        result = asyncio.run(
            resolve_poll_question(
                owner_text="i want to check with the team if they have filled their timesheet for this week",
                parsed_question="i want to check with the team if they have filled their timesheet for this week?",
                parsed_mode={"response_format": "free_text", "options": [], "deadline_mode": "default"},
                project_name="demo",
                project_timezone="Asia/Kolkata",
            )
        )
    finally:
        settings.team_poll_question_rewrite_enabled = prev_enabled
        settings.team_poll_question_rewrite_max_retries = prev_retries
        settings.team_poll_question_rewrite_backoff_ms = prev_backoff
        settings.team_poll_question_rewrite_retry_jitter_ms = prev_jitter
        settings.team_poll_question_rewrite_min_confidence = prev_min_conf

    assert attempts["count"] == 3
    assert result["source"] == "llm"
    assert result["question_text"] == "Did you submit your timesheet for 2026-03-02 to 2026-03-08?"


def test_resolve_poll_question_fallback_after_low_confidence_retries(monkeypatch):
    prev_enabled = settings.team_poll_question_rewrite_enabled
    prev_retries = settings.team_poll_question_rewrite_max_retries
    prev_backoff = settings.team_poll_question_rewrite_backoff_ms
    prev_jitter = settings.team_poll_question_rewrite_retry_jitter_ms
    prev_min_conf = settings.team_poll_question_rewrite_min_confidence
    attempts = {"count": 0}

    async def _fake_rewrite(**kwargs):
        attempts["count"] += 1
        return {
            "question_text": "Did you complete timesheet for 2026-03-02 to 2026-03-08?",
            "confidence": 0.25,
            "model": "unit-test-model",
        }

    async def _fake_sleep(_seconds):
        return None

    try:
        settings.team_poll_question_rewrite_enabled = True
        settings.team_poll_question_rewrite_max_retries = 2
        settings.team_poll_question_rewrite_backoff_ms = "1,1"
        settings.team_poll_question_rewrite_retry_jitter_ms = 0
        settings.team_poll_question_rewrite_min_confidence = 0.8
        monkeypatch.setattr(question_rewriter, "_rewrite_with_llm", _fake_rewrite)
        monkeypatch.setattr(question_rewriter.asyncio, "sleep", _fake_sleep)

        result = asyncio.run(
            resolve_poll_question(
                owner_text="i want to check with the team if they have filled their timesheet for this week",
                parsed_question="i want to check with the team if they have filled their timesheet for this week?",
                parsed_mode={"response_format": "free_text", "options": [], "deadline_mode": "default"},
                project_name="demo",
                project_timezone="Asia/Kolkata",
            )
        )
    finally:
        settings.team_poll_question_rewrite_enabled = prev_enabled
        settings.team_poll_question_rewrite_max_retries = prev_retries
        settings.team_poll_question_rewrite_backoff_ms = prev_backoff
        settings.team_poll_question_rewrite_retry_jitter_ms = prev_jitter
        settings.team_poll_question_rewrite_min_confidence = prev_min_conf

    assert attempts["count"] == 3
    assert result["source"] == "deterministic"
    assert result["question_text"] == "i want to check with the team if they have filled their timesheet for this week?"
