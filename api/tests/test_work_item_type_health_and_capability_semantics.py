import unittest
from importlib import util
from pathlib import Path
import sys
import types


def _load_module(module_path: Path, module_name: str):
    spec = util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_path}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROJECTS_DIR = Path(__file__).resolve().parents[1] / "app" / "projects"
_APP_DIR = _PROJECTS_DIR.parent

if "app" not in sys.modules:
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(_APP_DIR)]
    sys.modules["app"] = app_pkg

if "app.projects" not in sys.modules:
    projects_pkg = types.ModuleType("app.projects")
    projects_pkg.__path__ = [str(_PROJECTS_DIR)]
    sys.modules["app.projects"] = projects_pkg

# Preload health_rules under the canonical import path used by health service.
_load_module(_PROJECTS_DIR / "health_rules.py", "app.projects.health_rules")

_HEALTH_SERVICE_MODULE = _load_module(_PROJECTS_DIR / "health_service.py", "app.projects.health_service")
_CAPABILITY_DETECTOR_MODULE = _load_module(
    _PROJECTS_DIR / "capability_detector.py",
    "app.projects.capability_detector",
)
_HEALTH_CALCULATOR_MODULE = _load_module(
    _PROJECTS_DIR / "health_calculator.py",
    "app.projects.health_calculator",
)

HealthService = _HEALTH_SERVICE_MODULE.HealthService
WorkflowCapabilityDetector = _CAPABILITY_DETECTOR_MODULE.WorkflowCapabilityDetector
ProjectHealthCalculator = _HEALTH_CALCULATOR_MODULE.ProjectHealthCalculator


class WorkItemTypeHealthAndCapabilitySemanticsTests(unittest.TestCase):
    def test_health_service_uses_work_item_type_for_hierarchy_detection(self):
        tasks = [
            {
                "external_id": "PRM-1",
                "title": "Platform Rollout",
                "work_item_type": "portfolio",
                "status": "in_progress",
            },
            {
                "external_id": "PRM-2",
                "title": "Implement API",
                "work_item_type": "standard",
                "parent_key": "PRM-1",
                "status": "in_progress",
            },
        ]

        payload = HealthService.calculate_health(tasks)

        self.assertEqual(payload.get("health_type"), "epic")
        self.assertEqual(len(payload.get("epics") or []), 1)

    def test_capability_detector_uses_work_item_type_for_epic_detection(self):
        tasks = [
            {"external_id": "PRM-1", "work_item_type": "portfolio"},
            {"external_id": "PRM-2", "work_item_type": "standard", "parent_key": "PRM-1"},
        ]

        capabilities = WorkflowCapabilityDetector.detect_capabilities(tasks)

        self.assertTrue(capabilities.get("has_epics"))
        self.assertIn("epic", capabilities.get("grouping_hierarchy") or [])

    def test_health_calculator_excludes_portfolio_items_from_task_mode(self):
        tasks = [
            {
                "external_id": "PRM-1",
                "title": "Program",
                "work_item_type": "portfolio",
                "status": "in_progress",
            },
            {
                "external_id": "PRM-2",
                "title": "Execution item",
                "work_item_type": "standard",
                "status": "in_progress",
            },
        ]

        payload = ProjectHealthCalculator.calculate_task_based_health(tasks)

        self.assertEqual(payload.get("summary", {}).get("total_tasks"), 1)
        self.assertEqual(len(payload.get("tasks") or []), 1)

    def test_health_service_summary_scope_uses_standard_work_item_type(self):
        tasks = [
            {
                "external_id": "PRM-100",
                "title": "Provider custom standard work",
                "work_item_type": "standard",
                "provider_type": "Issue",
                "status": "in_progress",
            },
            {
                "external_id": "PRM-101",
                "title": "Defect item",
                "work_item_type": "defect",
                "status": "in_progress",
            },
        ]

        payload = HealthService.calculate_health(tasks)

        # In flat mode summary scope should still include standard work items
        # even when provider_type is not "Story".
        self.assertEqual(payload.get("health_type"), "task")
        self.assertEqual(payload.get("summary", {}).get("total_tasks"), 1)
        self.assertEqual(payload.get("kpi_scope"), "standard")

    def test_health_service_preserves_raw_status_for_display(self):
        item = {
            "external_id": "PRM-300",
            "title": "Verify QA transition path",
            "status": "in_progress",
            "status_raw": "QA TEST",
            "status_display": "QA TEST",
            "work_item_type": "defect",
            "issue_type": "Bug",
        }

        formatted = HealthService._format_work_item(item)
        self.assertEqual(formatted.get("status"), "in_progress")
        self.assertEqual(formatted.get("status_raw"), "QA TEST")
        self.assertEqual(formatted.get("status_display"), "QA TEST")


if __name__ == "__main__":
    unittest.main()
