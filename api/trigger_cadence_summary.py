"""Trigger cadence daily summary generation for project proj-1699-5169.

This script:
1. Connects to MongoDB using the app's settings.
2. Finds all cadence sessions directly from the brain collection.
3. Builds session_summary on any session that is missing one.
4. Directly assembles and stores a cadence_daily_summary document.

Run from the api/ directory:
    .\venv\Scripts\python.exe trigger_cadence_summary.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

PROJECT_ID = "proj-1699-5169"
DAILY_SUMMARY_ENTITY_TYPE = "cadence_daily_summary"
SESSION_ENTITY_TYPE = "cadence_session"


async def main():
    # ---- 1. Connect to MongoDB -------------------------------------------
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client.get_default_database()
    brain_db = client[settings.brain_db_name]
    brain_col = brain_db[PROJECT_ID]

    # Also inject into database module so helpers work
    import app.core.database as db_module
    db_module.client = client
    db_module.database = db
    db_module.brain_db = brain_db

    print(f"Connected to MongoDB: {db.name}")
    print(f"Brain DB: {brain_db.name}")
    print(f"Project: {PROJECT_ID}")
    print("=" * 60)

    # ---- 2. Find ALL cadence sessions in brain collection ----------------
    sessions = await brain_col.find({
        "entity_type": SESSION_ENTITY_TYPE,
    }).to_list(length=200)

    print(f"Found {len(sessions)} cadence session(s) in brain collection")

    if not sessions:
        # Also dump entity types for debugging
        pipeline = [{"$group": {"_id": "$entity_type"}}, {"$limit": 20}]
        entity_types = await brain_col.aggregate(pipeline).to_list(length=20)
        print(f"Entity types in brain/{PROJECT_ID}: {[e['_id'] for e in entity_types]}")
        client.close()
        return

    for s in sessions:
        print(f"  session_id={s.get('session_id', 'N/A')}")
        print(f"    status={s.get('status', 'N/A')}")
        print(f"    user_id={s.get('user_id', 'N/A')}")
        print(f"    correlation_id={s.get('correlation_id', 'NONE')}")
        print(f"    created_at={s.get('created_at', 'N/A')}")
        print(f"    has_session_summary={'session_summary' in s}")
        tasks = s.get("tasks") or []
        print(f"    tasks={len(tasks)}")

    # ---- 3. Build session_summary for sessions missing it ----------------
    for session in sessions:
        sid = session.get("session_id", "")
        if "session_summary" in session:
            print(f"\n  ✓ Session {sid} already has session_summary")
            continue

        member_name = session.get("user_id", "Unknown")
        tasks = session.get("tasks") or []

        tasks_completed = []
        tasks_updated = []
        skipped = 0
        comments = []

        for task in tasks:
            ext_id = task.get("external_id", "")
            title = task.get("title", "")
            original_status = task.get("status", "")
            updated_status = task.get("updated_status")
            comment = task.get("comment", "")

            status_changed = updated_status is not None and updated_status != original_status
            has_comment = bool(comment and comment.strip())

            if status_changed and updated_status == "done":
                tasks_completed.append({"key": ext_id, "title": title})
            elif status_changed:
                tasks_updated.append({
                    "key": ext_id, "title": title,
                    "old_status": original_status, "new_status": updated_status,
                })
            elif not has_comment:
                skipped += 1

            if has_comment:
                comments.append({"task_key": ext_id, "comment": comment})

        key_input = None
        if comments:
            key_input = "; ".join(
                f"{c['task_key']}: {c['comment'].strip()[:60]}" for c in comments[:3]
            )

        summary = {
            "member_name": member_name,
            "responded": True,
            "tasks_completed": tasks_completed,
            "tasks_updated": tasks_updated,
            "tasks_skipped": skipped,
            "key_input": key_input,
            "backlog_action": session.get("backlog_action"),
            "backlog_items_picked": session.get("backlog_items_picked"),
            "generated_at": datetime.utcnow(),
        }

        await brain_col.update_one(
            {"_id": session["_id"]},
            {"$set": {"session_summary": summary}},
        )
        session["session_summary"] = summary  # update in-memory too
        print(f"\n  ✓ Built session_summary for {sid} (member: {member_name})")
        print(f"    completed={len(tasks_completed)}, updated={len(tasks_updated)}, skipped={skipped}")

    # ---- 4. Assemble the daily summary document directly -----------------
    # Group by correlation_id (or by date if no correlation_id)
    synthetic_cid = "trigger-script-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")

    responded_names = []
    not_responded_names = []
    task_progress = []
    key_inputs = []
    backlog = []
    total_completed = 0
    total_updated = 0

    for session in sessions:
        summary = session.get("session_summary") or {}
        member_name = summary.get("member_name") or session.get("user_id", "Unknown")

        if summary.get("responded", False):
            responded_names.append(member_name)

            completed = summary.get("tasks_completed") or []
            updated = summary.get("tasks_updated") or []
            skipped = summary.get("tasks_skipped", 0)

            completed_strs = [f"{t['key']} ({t['title']})" for t in completed]
            updated_strs = []
            for t in updated:
                new_status = (t.get("new_status") or "").replace("_", " ").title()
                updated_strs.append(f"{t['key']} -> {new_status}")

            total_completed += len(completed)
            total_updated += len(updated)

            task_progress.append({
                "member": member_name,
                "completed": completed_strs,
                "updated": updated_strs,
                "skipped": skipped,
            })

            ki = summary.get("key_input")
            if ki:
                key_inputs.append({"member": member_name, "input": ki})

            bl_action = summary.get("backlog_action")
            bl_picked = summary.get("backlog_items_picked") or []
            if bl_action == "picked_up" and bl_picked:
                items_str = ", ".join(f"{t['key']} ({t['title']})" for t in bl_picked)
                backlog.append({"member": member_name, "action": f"Picked up {items_str} from backlog"})
            elif bl_action == "deferred":
                backlog.append({"member": member_name, "action": "Deferred backlog items"})
        else:
            not_responded_names.append(member_name)

    total_members = len(responded_names) + len(not_responded_names)
    rate = round(len(responded_names) / total_members, 2) if total_members else 0

    overall_parts = [f"{len(responded_names)} of {total_members} members responded."]
    if total_completed:
        overall_parts.append(f"{total_completed} task(s) completed.")
    if total_updated:
        overall_parts.append(f"{total_updated} status change(s).")
    if not_responded_names:
        overall_parts.append(f"Follow-up may be needed with {', '.join(not_responded_names)}.")
    overall = " ".join(overall_parts)

    # Use today's date
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    # Try to use the session's created_at date
    for s in sessions:
        created = s.get("created_at")
        if created and isinstance(created, datetime):
            date_str = created.strftime("%Y-%m-%d")
            break

    # Use correlation_id from session if available, otherwise synthetic
    correlation_id = None
    for s in sessions:
        c = s.get("correlation_id")
        if c:
            correlation_id = c
            break
    if not correlation_id:
        correlation_id = synthetic_cid

    summary_doc = {
        "entity_type": DAILY_SUMMARY_ENTITY_TYPE,
        "project_id": PROJECT_ID,
        "correlation_id": correlation_id,
        "date": date_str,
        "generated_at": datetime.utcnow(),
        "owner_notified": False,
        "participation": {
            "responded": responded_names,
            "not_responded": not_responded_names,
            "total": total_members,
            "rate": rate,
        },
        "task_progress": task_progress,
        "key_inputs": key_inputs,
        "backlog": backlog,
        "overall": overall,
    }

    result = await brain_col.insert_one(summary_doc)
    print("\n" + "=" * 60)
    print(f"✅ Daily summary stored!")
    print(f"  _id: {result.inserted_id}")
    print(f"  date: {date_str}")
    print(f"  correlation_id: {correlation_id}")
    print(f"  responded: {responded_names}")
    print(f"  overall: {overall}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
