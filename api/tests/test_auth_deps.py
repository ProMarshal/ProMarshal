import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.core.auth_deps import get_current_user


def _build_request(path: str, query: str = "") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "query_string": query.encode("utf-8"),
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)


class AuthDepsTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_bypass_allows_missing_auth(self):
        request = _build_request("/api/projects/timezones")
        result = await get_current_user(request=request, authorization=None, x_user_id=None)
        self.assertEqual(result, {"user_id": "", "name": "", "sub": ""})

    async def test_strict_mode_rejects_legacy_header_without_bearer(self):
        request = _build_request("/api/projects/abc123/members")
        with patch("app.core.auth_deps.settings.jwt_enforcement_enabled", True):
            with self.assertRaises(HTTPException) as context:
                await get_current_user(
                    request=request,
                    authorization=None,
                    x_user_id="legacy-user",
                )
        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Missing Authorization header")

    async def test_compat_mode_accepts_x_user_id_fallback(self):
        request = _build_request("/api/projects/abc123/members")
        with patch("app.core.auth_deps.settings.jwt_enforcement_enabled", False):
            result = await get_current_user(
                request=request,
                authorization=None,
                x_user_id="legacy-user",
            )
        self.assertEqual(result["user_id"], "legacy-user")
        self.assertEqual(result["sub"], "legacy-user")

    async def test_compat_mode_accepts_query_user_id_fallback(self):
        request = _build_request("/api/projects/abc123/members", query="user_id=query-user")
        with patch("app.core.auth_deps.settings.jwt_enforcement_enabled", False):
            result = await get_current_user(
                request=request,
                authorization=None,
                x_user_id=None,
            )
        self.assertEqual(result["user_id"], "query-user")
        self.assertEqual(result["sub"], "query-user")

    async def test_bearer_token_decodes_payload(self):
        request = _build_request("/api/projects/abc123/members")
        with patch("app.core.auth_deps.decode_access_token", return_value={"user_id": "u-1", "name": "Alice", "sub": "u-1"}):
            result = await get_current_user(
                request=request,
                authorization="Bearer test-token",
                x_user_id=None,
            )
        self.assertEqual(result, {"user_id": "u-1", "name": "Alice", "sub": "u-1"})


if __name__ == "__main__":
    unittest.main()
