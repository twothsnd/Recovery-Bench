from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from recovery_bench.agents.provider import ModelResponse, ProviderAgent, build_provider_agent
from recovery_bench.agents.provider import (
    _anthropic_api_key,
    _anthropic_request_options,
    _extract_anthropic_text,
    _extract_chat_completion_text,
    _openai_client_options,
    _openai_request_options,
    _vllm_base_url,
    _vllm_request_options,
)
from recovery_bench.agents.registry import AgentRegistry, default_agent_registry
from recovery_bench.config import AgentConfig, ModelConfig
from recovery_bench.types import AgentContext, AgentRunResult, Task


@dataclass(slots=True)
class FakeClient:
    provider: str = "fake"
    model: str = "fake-model"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        self.calls.append((prompt, context.task_id))
        return ModelResponse(text=f"response for {context.task_id}", metadata={"fake": True})


class BridgeEnvironment:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_recovery_bench_agent(
        self,
        *,
        prompt: str,
        model_client: FakeClient,
        context: AgentContext,
        options: dict[str, Any],
    ) -> AgentRunResult:
        response = model_client.complete(prompt, context=context)
        self.calls.append({"prompt": prompt, "options": options, "response": response.text})
        return AgentRunResult(metadata={"bridge_seen": True, "response_text": response.text})


def _context() -> AgentContext:
    return AgentContext(
        benchmark="bridge-bench",
        task_id="task-1",
        protocol="recovery",
        attempt_index=2,
        k=3,
    )


def test_provider_agent_delegates_to_benchmark_bridge() -> None:
    client = FakeClient()
    agent = ProviderAgent(name="fake-agent", client=client, options={"max_steps": 12})
    task = Task(task_id="task-1", prompt="original")
    env = BridgeEnvironment()

    result = agent.run(task, "attempt prompt", env, _context())

    assert result.error is None
    assert result.metadata["agent"] == "fake-agent"
    assert result.metadata["provider"] == "fake"
    assert result.metadata["model"] == "fake-model"
    assert result.metadata["bridge_seen"] is True
    assert env.calls == [
        {
            "prompt": "attempt prompt",
            "options": {"max_steps": 12},
            "response": "response for task-1",
        }
    ]
    assert client.calls == [("attempt prompt", "task-1")]


def test_provider_agent_without_bridge_fails_clearly() -> None:
    agent = ProviderAgent(name="fake-agent", client=FakeClient())
    task = Task(task_id="task-1", prompt="original")

    result = agent.run(task, "attempt prompt", object(), _context())

    assert result.error is not None
    assert "benchmark-native execution bridge" in result.error
    assert result.metadata["available_bridge_methods"] == []


def test_build_provider_agent_rejects_provider_mismatch() -> None:
    with pytest.raises(ValueError, match="expects model.provider='openai'"):
        build_provider_agent(
            name="openai-agent",
            expected_provider="openai",
            model_config=ModelConfig(name="claude-sonnet", provider="anthropic"),
            client_factory=lambda _config: FakeClient(provider="openai", model="gpt"),
        )


def test_agent_registry_build_passes_model_config_and_agent_options() -> None:
    captured = {}
    registry = AgentRegistry()

    def factory(model: ModelConfig, agent: AgentConfig) -> ProviderAgent:
        captured["model"] = model
        captured["agent"] = agent
        return ProviderAgent(name=agent.name, client=FakeClient(provider=model.provider, model=model.name))

    model_config = ModelConfig(name="gpt-4.1", provider="openai", options={"temperature": 0})
    agent_config = AgentConfig(name="custom-agent", options={"max_steps": 8})
    registry.register("custom-agent", factory)

    adapter = registry.build("custom-agent", model_config=model_config, agent_config=agent_config)

    assert adapter.name == "custom-agent"
    assert captured["model"] == model_config
    assert captured["agent"] == agent_config


def test_default_provider_agents_are_registered_with_dependency_status() -> None:
    rows = {name: status for name, status, _reason in default_agent_registry().describe()}
    assert rows["openai-agent"] in {"available", "unavailable"}
    assert rows["anthropic-agent"] in {"available", "unavailable"}
    assert rows["gemini-agent"] in {"available", "unavailable"}
    assert rows["vllm-agent"] in {"available", "unavailable"}


def test_openai_client_options_can_use_codex_auth_and_config(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    config_path = tmp_path / "config.toml"
    auth_path.write_text('{"OPENAI_API_KEY": "sk-test"}', encoding="utf-8")
    config_path.write_text(
        """
model_provider = "custom"

[model_providers.custom]
base_url = "https://example.test/v1"
wire_api = "responses"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    options = {
        "codex_auth_path": str(auth_path),
        "codex_config_path": str(config_path),
        "developer_message": "return text",
        "temperature": 0,
    }

    assert _openai_client_options(options) == {
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
    }
    assert _openai_request_options(options) == {"temperature": 0}


def test_openai_client_options_can_use_env_base_url_with_suffix(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic-compatible.example.test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    options = {
        "api_key_env": "ANTHROPIC_AUTH_TOKEN",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "base_url_suffix": "/v1",
        "use_codex_auth": False,
        "use_codex_config": False,
        "temperature": 0,
    }

    assert _openai_client_options(options) == {
        "api_key": "sk-ant-test",
        "base_url": "https://anthropic-compatible.example.test/v1",
    }
    assert _openai_request_options(options) == {"temperature": 0}


def test_anthropic_options_support_auth_token_fallback(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-test")

    options = {
        "base_url": "https://anthropic.example.test",
        "anthropic_version": "2023-06-01",
        "timeout": 10,
        "temperature": 0,
    }

    assert _anthropic_api_key(options) == "sk-ant-test"
    assert _anthropic_request_options(options) == {"temperature": 0}


def test_extract_anthropic_text_from_http_fallback_response() -> None:
    response = {
        "content": [
            {"type": "thinking"},
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
    }

    assert _extract_anthropic_text(response) == "hello\nworld"


def test_vllm_options_normalize_openai_compatible_base_url(monkeypatch) -> None:
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    options = {
        "base_url": "http://127.0.0.1:8000/v1",
        "timeout": 10,
        "temperature": 0,
    }

    assert _vllm_base_url(options) == "http://127.0.0.1:8000/v1"
    assert _vllm_request_options(options) == {"temperature": 0}


def test_extract_chat_completion_text_from_vllm_response() -> None:
    response = {"choices": [{"message": {"content": "```python\nimport pyautogui\n```"}}]}

    assert _extract_chat_completion_text(response) == "```python\nimport pyautogui\n```"
