"""Benchmark /api/cron/tick latency and scheduler throughput.

Usage (PowerShell):
  cd api
  .\\venv\\Scripts\\python .\\scripts\\benchmark_dispatcher_tick.py `
    --base-url http://127.0.0.1:8000 `
    --cron-secret YOUR_CRON_SECRET `
    --requests 30 `
    --concurrency 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from queue import Queue
from typing import Dict, List, Optional


@dataclass
class TickResult:
    ok: bool
    status_code: int
    latency_ms: float
    processed_total: int
    success_total: int
    failed_total: int
    skipped_total: int
    error: Optional[str] = None


def _sum_lane_counts(payload: Dict[str, object], key: str) -> int:
    total = 0
    for lane_key in ("fast_lane_results", "daily_lane_results"):
        lane = payload.get(lane_key) or {}
        if not isinstance(lane, dict):
            continue
        for result in lane.values():
            if isinstance(result, dict):
                total += int(result.get(key) or 0)
    return total


def _post_tick(base_url: str, cron_secret: str, timeout_seconds: int) -> TickResult:
    url = f"{base_url.rstrip('/')}/api/cron/tick"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {cron_secret}",
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
            latency_ms = (time.perf_counter() - start) * 1000.0
            payload = json.loads(body) if body else {}
            processed = _sum_lane_counts(payload, "processed")
            success = _sum_lane_counts(payload, "success")
            failed = _sum_lane_counts(payload, "failed")
            skipped = _sum_lane_counts(payload, "skipped")
            return TickResult(
                ok=(200 <= resp.status < 300),
                status_code=int(resp.status),
                latency_ms=latency_ms,
                processed_total=processed,
                success_total=success,
                failed_total=failed,
                skipped_total=skipped,
            )
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return TickResult(
            ok=False,
            status_code=int(exc.code or 0),
            latency_ms=latency_ms,
            processed_total=0,
            success_total=0,
            failed_total=0,
            skipped_total=0,
            error=f"http_error:{exc}",
        )
    except Exception as exc:  # pragma: no cover - network/runtime failure path
        latency_ms = (time.perf_counter() - start) * 1000.0
        return TickResult(
            ok=False,
            status_code=0,
            latency_ms=latency_ms,
            processed_total=0,
            success_total=0,
            failed_total=0,
            skipped_total=0,
            error=str(exc),
        )


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    return float(sorted_vals[max(0, min(idx, len(sorted_vals) - 1))])


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark /api/cron/tick endpoint")
    parser.add_argument("--base-url", required=True, help="API base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--cron-secret", required=True, help="CRON_SECRET value")
    parser.add_argument("--requests", type=int, default=30, help="Total requests to run")
    parser.add_argument("--concurrency", type=int, default=3, help="Parallel workers")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="HTTP timeout per request")
    args = parser.parse_args()

    total = max(1, int(args.requests))
    concurrency = max(1, int(args.concurrency))
    queue: Queue[int] = Queue()
    for i in range(total):
        queue.put(i)

    lock = threading.Lock()
    results: List[TickResult] = []

    def worker() -> None:
        while True:
            try:
                queue.get_nowait()
            except Exception:
                return
            result = _post_tick(
                base_url=args.base_url,
                cron_secret=args.cron_secret,
                timeout_seconds=max(5, int(args.timeout_seconds)),
            )
            with lock:
                results.append(result)
            queue.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration_s = max(0.001, (time.perf_counter() - started))

    latencies = [r.latency_ms for r in results]
    ok_count = sum(1 for r in results if r.ok)
    processed_sum = sum(r.processed_total for r in results)
    success_sum = sum(r.success_total for r in results)
    failed_sum = sum(r.failed_total for r in results)
    skipped_sum = sum(r.skipped_total for r in results)
    errors = [r.error for r in results if r.error]

    summary = {
        "requests_total": total,
        "requests_ok": ok_count,
        "requests_failed": total - ok_count,
        "duration_seconds": round(duration_s, 3),
        "rps": round(total / duration_s, 2),
        "latency_ms": {
            "p50": round(_percentile(latencies, 50), 2),
            "p95": round(_percentile(latencies, 95), 2),
            "p99": round(_percentile(latencies, 99), 2),
            "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "scheduler_counts": {
            "processed_total": processed_sum,
            "success_total": success_sum,
            "failed_total": failed_sum,
            "skipped_total": skipped_sum,
        },
        "sample_errors": errors[:5],
    }
    print(json.dumps(summary, indent=2))

    # Basic pass criteria for dispatcher health benchmark:
    # - all requests succeed
    # - p95 under 3000ms in local/staging baseline
    # - scheduler failures should be 0 in steady state
    pass_ok = (
        summary["requests_failed"] == 0
        and summary["latency_ms"]["p95"] < 3000
        and summary["scheduler_counts"]["failed_total"] == 0
    )
    print(f"\nPASS={pass_ok}")
    return 0 if pass_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
