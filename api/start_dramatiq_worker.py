#!/usr/bin/env python3
"""Dramatiq worker launcher for ProMarshal."""

from __future__ import annotations

import os
import subprocess
import sys


SUPPORTED_QUEUES = {
    "chat_interactions",
    "workitem_events",
    "chat_commands",
    "cortex_runs",
    "action_item_extraction",
    "cadence_dm",
}


def _normalize_queues(args: list[str]) -> list[str]:
    if not args:
        return sorted(SUPPORTED_QUEUES)
    unknown = sorted(set(args) - SUPPORTED_QUEUES)
    if unknown:
        print(f"ERROR: Unsupported queue(s): {', '.join(unknown)}")
        print(f"Supported queues: {', '.join(sorted(SUPPORTED_QUEUES))}")
        sys.exit(1)
    return args


def main() -> int:
    queue_names = _normalize_queues(sys.argv[1:])
    workers = str(os.getenv("DRAMATIQ_WORKER_PROCESSES", "1")).strip() or "1"
    threads = str(os.getenv("DRAMATIQ_WORKER_THREADS", "8")).strip() or "8"
    cmd = [
        sys.executable,
        "-m",
        "dramatiq",
        "app.workers.dramatiq_app",
        "--processes",
        workers,
        "--threads",
        threads,
        "--queues",
    ]
    cmd.extend(queue_names)
    print(f"Starting Dramatiq worker: {' '.join(cmd)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
