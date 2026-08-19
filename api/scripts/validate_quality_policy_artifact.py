"""Validate Cortex quality policy artifact schema."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.cortex.quality.policy_registry import load_quality_policy_artifact


def main() -> int:
    settings.cortex_quality_policy_registry_enabled = True
    settings.cortex_quality_policy_fail_fast = True
    artifact = load_quality_policy_artifact(force_reload=True)
    if artifact is None:
        raise RuntimeError("quality_policy_artifact_not_loaded")
    print(
        "quality_policy_artifact_valid "
        f"schema_version={artifact.schema_version} "
        f"field_class_defaults={len(artifact.field_class_defaults)} "
        f"tool_field_overrides={len(artifact.tool_field_overrides)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
