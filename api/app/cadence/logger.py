"""Logging utilities for task reminder system"""
import logging
from datetime import datetime
from typing import Optional

# Configure logger
logger = logging.getLogger("task_reminders")
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Format: [2025-12-31 09:00:00] INFO: Message
formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(formatter)

logger.addHandler(console_handler)


def log_reminder_sent(
    project_id: str,
    member_email: str,
    task_count: int,
    tier: str,
    correlation_id: Optional[str] = None
):
    """Log when a reminder is successfully sent"""
    msg = f"Reminder sent | Project: {project_id} | Member: {member_email} | Tasks: {task_count} | Tier: {tier}"
    if correlation_id:
        msg += f" | CorrelationID: {correlation_id}"
    logger.info(msg)


def log_reminder_failed(
    project_id: str,
    member_email: str,
    error: str,
    correlation_id: Optional[str] = None
):
    """Log when a reminder fails"""
    msg = f"Reminder failed | Project: {project_id} | Member: {member_email} | Error: {error}"
    if correlation_id:
        msg += f" | CorrelationID: {correlation_id}"
    logger.error(msg)


def log_project_processed(project_id: str, project_name: str, member_count: int, tier: str):
    """Log when a project is processed"""
    logger.info(f"Processed project | ID: {project_id} | Name: {project_name} | Members: {member_count} | Tier: {tier}")


def log_project_skipped(project_id: str, reason: str):
    """Log when a project is skipped"""
    logger.info(f"Skipped project | ID: {project_id} | Reason: {reason}")


def log_cron_start(correlation_id: str):
    """Log when cron job starts"""
    logger.info(f"Cron job started | CorrelationID: {correlation_id}")


def log_cron_end(correlation_id: str, projects_processed: int, reminders_sent: int):
    """Log when cron job ends"""
    logger.info(f"Cron job completed | CorrelationID: {correlation_id} | Projects: {projects_processed} | Reminders: {reminders_sent}")
