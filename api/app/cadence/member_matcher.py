"""Match tasks to members using accountId"""
from typing import List, Dict, Any


def match_tasks_to_members(tasks: List[Dict[str, Any]], members: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group tasks by member using accountId matching

    Args:
        tasks: List of task documents from DB
        members: List of project members

    Returns:
        Dictionary mapping member email to their tasks
        Example: {
            "prabhu@gmail.com": [{task1}, {task2}],
            "sridevi@gmail.com": [{task3}]
        }
    """
    member_tasks = {}

    # Loop through each task
    for task in tasks:
        task_assignee_id = task.get("assignee_account_id")

        if not task_assignee_id:
            continue

        # Find matching member
        for member in members:
            # Get Jira account ID from integration_ids
            integration_ids = member.get("integration_ids", {})
            member_jira_id = integration_ids.get("jira_account_id")

            # Match found
            if member_jira_id and member_jira_id == task_assignee_id:
                member_email = member.get("email")

                if not member_email:
                    break

                # Initialize list if first task for this member
                if member_email not in member_tasks:
                    member_tasks[member_email] = []

                # Add task to member's list
                member_tasks[member_email].append(task)
                break

    return member_tasks


def get_member_by_email(members: List[Dict[str, Any]], email: str) -> Dict[str, Any]:
    """
    Find a member by email address

    Args:
        members: List of project members
        email: Member's email address

    Returns:
        Member document or None
    """
    for member in members:
        if member.get("email") == email:
            return member
    return None
