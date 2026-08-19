"""Check if it's time to send reminders for a project"""
from datetime import datetime
from typing import Dict, Any
import pytz


def should_send_reminder_now(project: Dict[str, Any], window_minutes: int = 10) -> bool:
    """
    Check if current time matches project's reminder time

    Uses a window to handle cron timing variations. With 30-minute reminder intervals
    (10:00, 10:30, 11:00, etc.), a 10-minute window prevents overlap while still
    allowing for GitHub Actions cron delays.

    Also checks last_reminder_sent to prevent duplicate reminders if cron runs
    multiple times within the same window.

    Args:
        project: Project document from MongoDB
        window_minutes: Time window to check (default: 10 minutes)

    Returns:
        True if reminder should be sent now, False otherwise
    """
    schedule = project.get("cadence_schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}

    if not schedule.get("enabled", True):
        return False

    schedule_spec = schedule.get("schedule_spec", {})
    if not isinstance(schedule_spec, dict):
        schedule_spec = {}
    reminder_time = str(schedule_spec.get("time") or "09:00")
    reminder_tz = str(project.get("timezone") or "UTC")

    try:
        # Get current time in project's timezone
        tz = pytz.timezone(reminder_tz)
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        now_in_project_tz = now_utc.astimezone(tz)

        # Parse reminder time
        reminder_hour, reminder_minute = map(int, reminder_time.split(":"))

        # Create reminder datetime for today in project timezone
        reminder_datetime = now_in_project_tz.replace(
            hour=reminder_hour,
            minute=reminder_minute,
            second=0,
            microsecond=0
        )

        # Calculate time difference
        time_diff = abs((now_in_project_tz - reminder_datetime).total_seconds() / 60)

        # Check if within window
        if time_diff > window_minutes:
            return False

        # Check if reminder was already sent recently (prevent duplicates)
        metadata = schedule.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        last_sent = metadata.get("last_dispatched_at")
        if last_sent:
            if isinstance(last_sent, str):
                last_sent = datetime.fromisoformat(last_sent.replace('Z', '+00:00'))
            elif not last_sent.tzinfo:
                last_sent = last_sent.replace(tzinfo=pytz.UTC)

            # If reminder was sent within the last 20 minutes, skip
            # (20 min = window_minutes * 2, to cover the full time slot)
            minutes_since_last = (now_utc - last_sent).total_seconds() / 60
            if minutes_since_last < 20:
                print(f"  Reminder already sent {minutes_since_last:.1f} min ago for project {project.get('_id')}")
                return False

        return True

    except Exception as e:
        print(f"Error checking reminder time for project {project.get('_id')}: {str(e)}")
        return False


def get_reminder_time_display(project: Dict[str, Any]) -> str:
    """
    Get human-readable reminder time for display

    Args:
        project: Project document from MongoDB

    Returns:
        Formatted time string (e.g., "09:00 IST")
    """
    schedule = project.get("cadence_schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    schedule_spec = schedule.get("schedule_spec", {})
    if not isinstance(schedule_spec, dict):
        schedule_spec = {}
    reminder_time = str(schedule_spec.get("time") or "09:00")
    reminder_tz = str(project.get("timezone") or "UTC")

    try:
        tz = pytz.timezone(reminder_tz)
        tz_abbr = datetime.now(tz).strftime('%Z')
        return f"{reminder_time} {tz_abbr}"
    except:
        return reminder_time
