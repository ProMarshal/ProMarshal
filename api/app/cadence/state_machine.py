"""State machine for paid tier conversation flow"""
from typing import Dict, Any, Optional


class StateMachine:
    """Manages state transitions for paid tier sessions"""

    VALID_STATES = [
        "awaiting_status",
        "awaiting_status_clarification",
        "awaiting_comment",
        "awaiting_overall_input",
        "awaiting_backlog_decision",
        "awaiting_backlog_pick",
        "awaiting_backlog_clarification",
        "completed",
    ]

    VALID_TRANSITIONS = {
        "awaiting_status": ["awaiting_comment", "awaiting_status_clarification"],
        "awaiting_status_clarification": ["awaiting_status", "awaiting_comment", "awaiting_overall_input", "completed"],
        "awaiting_comment": ["awaiting_status", "awaiting_overall_input", "completed"],
        "awaiting_overall_input": ["awaiting_backlog_decision", "completed"],
        "awaiting_backlog_decision": ["awaiting_backlog_pick", "awaiting_backlog_clarification", "completed"],
        "awaiting_backlog_pick": ["awaiting_backlog_pick", "awaiting_backlog_decision", "awaiting_backlog_clarification", "completed"],
        "awaiting_backlog_clarification": ["awaiting_backlog_decision", "awaiting_backlog_pick", "completed"],
        "completed": [],
    }

    @staticmethod
    def can_transition(current_state: str, new_state: str) -> bool:
        """
        Check if state transition is valid

        Args:
            current_state: Current session state
            new_state: Desired new state

        Returns:
            True if transition is valid
        """
        if current_state not in StateMachine.VALID_STATES:
            return False

        if new_state not in StateMachine.VALID_STATES:
            return False

        return new_state in StateMachine.VALID_TRANSITIONS.get(current_state, [])

    @staticmethod
    def get_next_state_after_status_update() -> str:
        """Get next state after user updates status"""
        return "awaiting_comment"

    @staticmethod
    def get_next_state_after_comment(has_more_tasks: bool) -> str:
        """
        Get next state after user submits comment

        Args:
            has_more_tasks: Whether there are more tasks to process

        Returns:
            Next state
        """
        if has_more_tasks:
            return "awaiting_status"
        else:
            return "completed"

    @staticmethod
    def validate_session_state(session: Dict[str, Any]) -> Optional[str]:
        """
        Validate session state

        Args:
            session: Session document

        Returns:
            Error message if invalid, None if valid
        """
        current_state = session.get("status")

        if current_state not in StateMachine.VALID_STATES:
            return f"Invalid state: {current_state}"

        current_task_index = session.get("current_task_index", 0)
        tasks = session.get("tasks", [])

        if current_task_index >= len(tasks):
            return "Task index out of bounds"

        return None
