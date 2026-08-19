from scripts.run_work_item_migration_rehearsal import _evaluate_rehearsal_exit_code


def _result(status: str = "completed") -> dict:
    return {"status": status}


def _report(checks: list[dict] | None = None, error: str = "", totals: dict | None = None) -> dict:
    return {"checks": checks or [], "error": error, "totals": totals or {}}


def test_rehearsal_exit_code_execute_fails_on_any_failed_check() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("completed"),
        report=_report(checks=[{"name": "no_task_records_remaining", "passed": False}]),
        dry_run=False,
    )
    assert exit_code == 1


def test_rehearsal_exit_code_dry_run_allows_write_dependent_failed_checks() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("completed"),
        report=_report(
            checks=[
                {"name": "no_task_records_remaining", "passed": False},
                {"name": "project_metadata_docs_coverage", "passed": False},
                {"name": "control_plane_marker_coverage", "passed": False},
            ]
        ),
        dry_run=True,
    )
    assert exit_code == 0


def test_rehearsal_exit_code_dry_run_fails_on_unexpected_integrity_check() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("completed"),
        report=_report(checks=[{"name": "no_unresolved_work_item_refs", "passed": False}]),
        dry_run=True,
    )
    assert exit_code == 1


def test_rehearsal_exit_code_dry_run_allows_unresolved_refs_when_task_records_remain() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("completed"),
        report=_report(
            checks=[{"name": "no_unresolved_work_item_refs", "passed": False}],
            totals={"task_records_remaining": 10},
        ),
        dry_run=True,
    )
    assert exit_code == 0


def test_rehearsal_exit_code_fails_on_report_error() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("completed"),
        report=_report(error="database_unavailable"),
        dry_run=True,
    )
    assert exit_code == 1


def test_rehearsal_exit_code_fails_when_migration_not_completed() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result=_result("failed"),
        report=_report(),
        dry_run=False,
    )
    assert exit_code == 1


def test_rehearsal_exit_code_dry_run_allows_failed_status_with_expected_last_error() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result={"status": "failed", "last_error": "one_or_more_projects_failed"},
        report=_report(
            checks=[{"name": "no_task_records_remaining", "passed": False}],
            totals={"task_records_remaining": 10},
        ),
        dry_run=True,
    )
    assert exit_code == 0


def test_rehearsal_exit_code_dry_run_fails_on_unexpected_last_error() -> None:
    exit_code = _evaluate_rehearsal_exit_code(
        result={"status": "failed", "last_error": "database_unavailable"},
        report=_report(),
        dry_run=True,
    )
    assert exit_code == 1
