"""
JWT enforcement cutover smoke checks.

Usage example:
  cd api
  .\\venv\\Scripts\\python.exe -m scripts.auth_cutover_smoke \
    --base-url https://api.promarshal.ai \
    --project-id <project_id> \
    --token <bearer_jwt> \
    --internal-secret <internal_secret>
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProbeResult:
    name: str
    expected: int
    actual: int
    ok: bool
    detail: str = ""


def _http_request(
    *,
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = 20.0,
) -> tuple[int, str]:
    req = urllib.request.Request(url=url, method=method, data=body)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return int(resp.getcode() or 0), payload
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        return int(exc.code or 0), payload
    except Exception as exc:  # network/ssl/dns failures
        return 0, f"{type(exc).__name__}: {exc}"


def _probe(
    *,
    name: str,
    method: str,
    url: str,
    expected_status: int,
    headers: Optional[dict[str, str]] = None,
    json_body: Optional[dict] = None,
) -> ProbeResult:
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers = {**(headers or {}), "Content-Type": "application/json"}
    status, payload = _http_request(
        method=method,
        url=url,
        headers=headers,
        body=body,
    )
    ok = status == expected_status
    detail = "" if ok else f"expected={expected_status} actual={status} payload={payload[:300]}"
    return ProbeResult(
        name=name,
        expected=expected_status,
        actual=status,
        ok=ok,
        detail=detail,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JWT cutover smoke checks for protected API ingress.")
    parser.add_argument("--base-url", required=True, help="API base URL, e.g. https://api.promarshal.ai")
    parser.add_argument("--project-id", required=True, help="Known project ObjectId for auth checks")
    parser.add_argument("--token", required=True, help="Valid bearer JWT for the target user")
    parser.add_argument(
        "--internal-secret",
        default="",
        help="INTERNAL_SERVICE_SECRET value for /api/auth/internal/token check",
    )
    parser.add_argument(
        "--skip-internal-check",
        action="store_true",
        help="Skip /api/auth/internal/token probe",
    )
    parser.add_argument(
        "--internal-email",
        default="staging-check@promarshal.ai",
        help="Email used for internal token exchange probe",
    )
    parser.add_argument(
        "--internal-name",
        default="Staging Check",
        help="Name used for internal token exchange probe",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Per-request timeout",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    base_url = str(args.base_url).rstrip("/")
    project_id = str(args.project_id).strip()
    token = str(args.token).strip()
    internal_secret = str(args.internal_secret or "").strip()
    now_ts = str(int(time.time()))

    results: list[ProbeResult] = []
    results.append(
        _probe(
            name="unauth_project_get_should_401",
            method="GET",
            url=f"{base_url}/api/projects/{project_id}",
            expected_status=401,
        )
    )
    results.append(
        _probe(
            name="auth_project_get_should_200",
            method="GET",
            url=f"{base_url}/api/projects/{project_id}",
            expected_status=200,
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    results.append(
        _probe(
            name="project_list_user_mismatch_should_403",
            method="GET",
            url=f"{base_url}/api/projects?user_id=__mismatch_probe__",
            expected_status=403,
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    if args.skip_internal_check:
        print("Skipping internal token exchange probe (--skip-internal-check set).")
    elif not internal_secret:
        print("Skipping internal token exchange probe (no --internal-secret provided).")
    else:
        results.append(
            _probe(
                name="internal_token_exchange_should_200",
                method="POST",
                url=f"{base_url}/api/auth/internal/token",
                expected_status=200,
                headers={
                    "X-Internal-Secret": internal_secret,
                    "X-Request-Timestamp": now_ts,
                },
                json_body={
                    "email": str(args.internal_email).strip(),
                    "name": str(args.internal_name).strip(),
                },
            )
        )

    passed = sum(1 for item in results if item.ok)
    failed = len(results) - passed
    print("JWT Cutover Smoke Summary")
    print(f"  total={len(results)} passed={passed} failed={failed}")
    for item in results:
        state = "PASS" if item.ok else "FAIL"
        print(f"  [{state}] {item.name} ({item.actual}/{item.expected})")
        if item.detail:
            print(f"         {item.detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
