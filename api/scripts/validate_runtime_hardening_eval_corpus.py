"""Validate runtime hardening offline eval corpus structure and minimum coverage."""

from __future__ import annotations

import json
from pathlib import Path


REQUIRED_FIELDS = {
    "id",
    "failure_class",
    "capability",
    "provider",
    "turn_type",
    "variant",
    "input",
    "expected",
}

MIN_FAILURE_CLASS_COUNTS = {
    "missing_required_slot": 3,
    "ambiguous_target_reference": 3,
    "unsafe_inferred_write": 2,
    "quality_false_block_on_explicit_text": 1,
}


def _root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _corpus_path() -> Path:
    return _root_dir() / "docs" / "testing" / "cortex-runtime-hardening-offline-corpus-v1.jsonl"


def validate() -> int:
    path = _corpus_path()
    if not path.exists():
        print(f"[FAIL] Eval corpus file not found: {path}")
        return 1

    seen_ids: set[str] = set()
    failure_counts: dict[str, int] = {}
    row_count = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            row_count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[FAIL] Invalid JSON at line {line_no}: {exc}")
                return 1
            if not isinstance(record, dict):
                print(f"[FAIL] Line {line_no} must be a JSON object.")
                return 1

            missing = [field for field in REQUIRED_FIELDS if field not in record]
            if missing:
                print(f"[FAIL] Line {line_no} missing required fields: {missing}")
                return 1

            sample_id = str(record.get("id") or "").strip()
            if not sample_id:
                print(f"[FAIL] Line {line_no} has empty id.")
                return 1
            if sample_id in seen_ids:
                print(f"[FAIL] Duplicate id found: {sample_id}")
                return 1
            seen_ids.add(sample_id)

            expected = record.get("expected")
            if not isinstance(expected, dict):
                print(f"[FAIL] Line {line_no} field `expected` must be an object.")
                return 1

            failure_class = str(record.get("failure_class") or "").strip()
            if not failure_class:
                print(f"[FAIL] Line {line_no} has empty failure_class.")
                return 1
            failure_counts[failure_class] = int(failure_counts.get(failure_class) or 0) + 1

    if row_count == 0:
        print("[FAIL] Eval corpus is empty.")
        return 1

    for failure_class, min_count in MIN_FAILURE_CLASS_COUNTS.items():
        actual = int(failure_counts.get(failure_class) or 0)
        if actual < min_count:
            print(
                "[FAIL] Insufficient coverage for failure class "
                f"`{failure_class}`: expected >= {min_count}, found {actual}."
            )
            return 1

    print(
        "[OK] Runtime hardening eval corpus validated: "
        f"samples={row_count} classes={len(failure_counts)} path={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(validate())
