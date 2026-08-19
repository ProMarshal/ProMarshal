import re
import unittest
from pathlib import Path


class FrontendAuthMigrationGuards(unittest.TestCase):
    def test_no_legacy_user_id_query_on_protected_api_calls(self):
        repo_root = Path(__file__).resolve().parents[2]
        web_root = repo_root / "web"
        protected_api_pattern = re.compile(
            r"/api/(projects|planner|integrations|action-items|agent|slack-messages)[^\"'`\s]*user_id=",
            re.IGNORECASE,
        )
        skip_files = {
            # server-side compatibility docs or non-runtime artifacts can be added here if needed
        }

        violations: list[str] = []
        for path in web_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root).as_posix()
            if "/.next/" in rel or "/node_modules/" in rel:
                continue
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
                continue
            if rel in skip_files:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if protected_api_pattern.search(text):
                violations.append(rel)

        self.assertEqual(
            violations,
            [],
            msg=(
                "Legacy '?user_id=' usage found in protected frontend API calls. "
                f"Files: {violations}"
            ),
        )

    def test_no_x_user_id_header_in_frontend(self):
        repo_root = Path(__file__).resolve().parents[2]
        web_root = repo_root / "web"
        violations: list[str] = []
        for path in web_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root).as_posix()
            if "/.next/" in rel or "/node_modules/" in rel:
                continue
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "X-User-Id" in text:
                violations.append(rel)

        self.assertEqual(
            violations,
            [],
            msg=(
                "Legacy X-User-Id header usage found in frontend. "
                f"Files: {violations}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
