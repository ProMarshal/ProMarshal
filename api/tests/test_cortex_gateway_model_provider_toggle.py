from types import SimpleNamespace
import asyncio

from app.agent_runtime import executor as executor_module
from app.agent_runtime.executor import AgentExecutor


def _build_executor_stub() -> AgentExecutor:
    executor = AgentExecutor.__new__(AgentExecutor)
    executor.runtime_config = SimpleNamespace(
        primary_provider="openai",
        primary_model="gpt-4o-mini",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
    )
    executor.telemetry = None
    return executor


def test_model_provider_uses_gateway_provider_by_default(monkeypatch):
    executor = _build_executor_stub()
    captured: dict[str, object] = {}

    class _FakeGatewayProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        executor_module,
        "GatewayToolCallingModelProvider",
        _FakeGatewayProvider,
        raising=False,
    )

    provider = executor._build_model_provider(
        execution_context={"project_id": "proj-1", "user_id": "u1", "session_key": "s1"}
    )

    assert isinstance(provider, _FakeGatewayProvider)
    assert captured.get("runtime_config") is executor.runtime_config
    assert captured.get("project_id") == "proj-1"
    assert callable(captured.get("on_health_event"))


def test_model_provider_uses_gateway_tool_call_provider_without_flag(monkeypatch):
    executor = _build_executor_stub()
    captured: dict[str, object] = {}

    class _FakeGatewayProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        executor_module,
        "GatewayToolCallingModelProvider",
        _FakeGatewayProvider,
        raising=False,
    )

    provider = executor._build_model_provider(
        execution_context={"project_id": "proj-1", "user_id": "u1", "session_key": "s1"}
    )

    assert isinstance(provider, _FakeGatewayProvider)
    assert captured.get("runtime_config") is executor.runtime_config
    assert captured.get("project_id") == "proj-1"
    assert callable(captured.get("on_health_event"))


def test_model_provider_uses_gateway_tool_call_provider_with_health_callback(
    monkeypatch,
):
    executor = _build_executor_stub()
    captured: dict[str, object] = {}

    class _FakeGatewayProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        executor_module,
        "GatewayToolCallingModelProvider",
        _FakeGatewayProvider,
        raising=False,
    )

    provider = executor._build_model_provider(
        execution_context={"project_id": "proj-1", "user_id": "u1", "session_key": "s1"}
    )

    assert isinstance(provider, _FakeGatewayProvider)
    assert captured.get("runtime_config") is executor.runtime_config
    assert captured.get("project_id") == "proj-1"
    assert callable(captured.get("on_health_event"))


def test_shadow_compare_skips_when_tools_present(monkeypatch):
    executor = _build_executor_stub()
    executor.telemetry = SimpleNamespace(emit=lambda *_args, **_kwargs: None)
    called = {"count": 0}

    async def _fake_run(**_kwargs):
        called["count"] += 1
        return ("compare", {"llm_path": "agents_litellm"})

    executor._run_with_agents_litellm = _fake_run
    asyncio.run(
        executor._maybe_emit_shadow_parity(
            payload={"tools": [{"name": "jira_search_work_items"}]},
            prompt="p",
            user_input="u",
            model_name="openai/gpt-4o-mini",
            execution_context={},
            primary_output="primary",
            primary_runtime_meta={"llm_path": "gateway_model_provider"},
        )
    )
    assert called["count"] == 0


def test_shadow_compare_noop_for_toolless_payload(monkeypatch):
    executor = _build_executor_stub()
    events = []
    executor.telemetry = SimpleNamespace(emit=lambda event: events.append(event))
    called = {"count": 0}

    async def _fake_run(**_kwargs):
        called["count"] += 1
        return ("compare-output", {"llm_path": "agents_litellm"})

    executor._run_with_agents_litellm = _fake_run
    asyncio.run(
        executor._maybe_emit_shadow_parity(
            payload={"tools": []},
            prompt="p",
            user_input="u",
            model_name="openai/gpt-4o-mini",
            execution_context={"project_id": "proj-1", "user_id": "u1", "session_key": "s1"},
            primary_output="primary-output",
            primary_runtime_meta={"llm_path": "gateway_model_provider"},
        )
    )
    assert called["count"] == 0
    assert events == []
