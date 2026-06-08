from __future__ import annotations

import json
import os
import time
import tomllib
import base64
from pathlib import Path
from inspect import Parameter, signature
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..config import ModelConfig
from ..errors import FatalRunError, raise_if_fatal_api_error
from ..types import ActionRecord, AgentCapabilities, AgentContext, AgentRunResult, Task


class ModelClient(Protocol):
    """One-shot text model client used by provider-backed agents."""

    provider: str
    model: str

    def complete(self, prompt: str, *, context: AgentContext) -> "ModelResponse":
        ...


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized model response kept provider-neutral for artifacts."""

    text: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderAgent:
    """Provider-backed agent that delegates benchmark interaction to native bridges.

    Benchmarks differ too much to safely infer action formats in the generic
    Recovery@k core. This adapter therefore supports explicit benchmark bridges:
    either the environment exposes a callable bridge method, or the run fails
    with a diagnostic error instead of pretending a plain model response changed
    the benchmark state.
    """

    name: str
    client: ModelClient
    options: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        task: Task,
        prompt: str,
        environment: Any,
        context: AgentContext,
    ) -> AgentRunResult:
        bridge = _find_bridge(environment, self.options)
        if bridge is None:
            available = _available_bridge_methods(environment)
            return AgentRunResult(
                metadata={
                    "agent": self.name,
                    "provider": self.client.provider,
                    "model": self.client.model,
                    "available_bridge_methods": available,
                },
                error=(
                    "ProviderAgent requires a benchmark-native execution bridge. "
                    "Expose run_recovery_bench_agent(...), run_with_model(...), or run_agent(...) "
                    "on benchmark.agent_environment(), or configure a benchmark-specific agent adapter."
                ),
            )

        try:
            result = _call_bridge(
                bridge,
                task=task,
                prompt=prompt,
                environment=environment,
                context=context,
                client=self.client,
                agent_options=self.options,
            )
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return AgentRunResult(
                metadata={
                    "agent": self.name,
                    "provider": self.client.provider,
                    "model": self.client.model,
                    "bridge": _callable_name(bridge),
                },
                error=f"Benchmark bridge failed: {type(exc).__name__}: {exc}",
            )

        return _normalize_agent_result(
            result,
            agent_name=self.name,
            provider=self.client.provider,
            model=self.client.model,
            bridge=_callable_name(bridge),
        )

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            memory_mode="benchmark_bridge",
            retry_memory_reset="previous_attempts_empty",
            recovery_memory="previous_attempts_forwarded_to_bridge",
            trajectory_export="action_records",
            official_agent="wrapped_model_client",
            metadata={"provider": self.client.provider, "model": self.client.model},
        )


@dataclass(slots=True)
class OpenAIModelClient:
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "openai"
    _client: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        client_options = _openai_client_options(self.options)
        if not client_options.get("api_key"):
            raise RuntimeError("OPENAI_API_KEY is not set and Codex OpenAI auth was not found")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("Python package 'openai' is not installed") from exc
        self._client = OpenAI(**client_options)

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        options = _openai_request_options(self.options)
        options.setdefault("model", self.model)
        if "input" not in options:
            developer_message = str(
                self.options.get("developer_message")
                or "You must produce a visible text response. Do not return only hidden reasoning."
            )
            options["input"] = [
                {"role": "developer", "content": developer_message},
                {"role": "user", "content": prompt},
            ]
        response = None
        text = ""
        empty_retries = int(self.options.get("empty_response_retries", 2))
        for empty_attempt in range(empty_retries + 1):
            try:
                response = self._responses_create_with_retries(options)
            except Exception as exc:
                raise_if_fatal_api_error(exc)
                raise
            text = getattr(response, "output_text", None)
            if text is None:
                text = _extract_openai_text(response)
            if str(text).strip() or empty_attempt >= empty_retries:
                break
            options = _openai_empty_retry_options(options)
        return ModelResponse(
            text=str(text),
            raw=response,
            metadata={"provider": self.provider, "model": self.model, "task_id": context.task_id},
        )

    def _responses_create_with_retries(self, options: dict[str, Any]) -> Any:
        retries = int(self.options.get("request_retries", 2))
        backoff = float(self.options.get("retry_backoff_seconds", 15.0))
        for attempt in range(retries + 1):
            try:
                return self._client.responses.create(**options)
            except Exception as exc:
                raise_if_fatal_api_error(exc)
                if attempt >= retries or not _is_retryable_openai_error(exc):
                    raise
                time.sleep(backoff * (2**attempt))
        raise RuntimeError("unreachable")


@dataclass(slots=True)
class AnthropicModelClient:
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "anthropic"
    _client: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not _anthropic_api_key(self.options):
            raise RuntimeError("ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is not set")
        try:
            import anthropic
        except Exception:
            self._client = None
            return
        client_kwargs = {"api_key": _anthropic_api_key(self.options)}
        base_url = _anthropic_base_url(self.options, default=None)
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**client_kwargs)

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        options = _anthropic_request_options(self.options)
        max_tokens = int(options.pop("max_tokens", 4096))
        if self._client is not None:
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                    **options,
                )
            except Exception as exc:
                raise_if_fatal_api_error(exc)
                raise
        else:
            try:
                response = _anthropic_messages_create(
                    model=self.model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    options=options,
                    client_options=self.options,
                )
            except Exception as exc:
                raise_if_fatal_api_error(exc)
                raise
        text = _extract_anthropic_text(response)
        return ModelResponse(
            text=text,
            raw=response,
            metadata={"provider": self.provider, "model": self.model, "task_id": context.task_id},
        )


@dataclass(slots=True)
class GeminiModelClient:
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "gemini"
    _client: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
        try:
            from google import genai
        except Exception as exc:
            raise RuntimeError("Python package 'google-genai' is not installed") from exc
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=api_key)

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        options = _normalize_gemini_options(self.options)
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                **options,
            )
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            raise
        text = getattr(response, "text", None) or _safe_str(response)
        return ModelResponse(
            text=text,
            raw=response,
            metadata={"provider": self.provider, "model": self.model, "task_id": context.task_id},
        )


@dataclass(slots=True)
class VLLMModelClient:
    """OpenAI-compatible chat.completions client for local vLLM servers."""

    model: str
    options: dict[str, Any] = field(default_factory=dict)
    provider: str = "vllm"

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        return self.complete_messages(
            [{"role": "user", "content": prompt}],
            context=context,
        )

    def complete_messages(self, messages: list[dict[str, Any]], *, context: AgentContext) -> ModelResponse:
        payload = self._request_payload(messages)
        response = self._post_chat_completions(payload)
        text = _extract_chat_completion_text(response)
        return ModelResponse(
            text=text,
            raw=response,
            metadata={"provider": self.provider, "model": self.model, "task_id": context.task_id},
        )

    def complete_with_image(
        self,
        prompt: str,
        image: bytes,
        *,
        context: AgentContext,
        mime_type: str = "image/png",
    ) -> ModelResponse:
        encoded = base64.b64encode(image).decode("ascii")
        return self.complete_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            context=context,
        )

    def _request_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = _vllm_request_options(self.options)
        payload.setdefault("model", self.model)
        payload["messages"] = messages
        return payload

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        base_url = _vllm_base_url(self.options)
        api_key = _vllm_api_key(self.options)
        headers = {"content-type": "application/json", "authorization": f"Bearer {api_key}"}
        timeout = float(self.options.get("timeout", 600.0))
        retries = int(self.options.get("request_retries", 2))
        backoff = float(self.options.get("retry_backoff_seconds", 10.0))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                if response.status_code in {401, 403}:
                    raise FatalRunError(f"fatal vLLM API response {response.status_code}: {response.text}")
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                raise_if_fatal_api_error(exc)
                last_error = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt >= retries or status_code not in {408, 409, 429, 500, 502, 503, 504}:
                    raise
                time.sleep(backoff * (2**attempt))
        raise RuntimeError(f"vLLM chat completion failed: {last_error}")


ClientFactory = Callable[[ModelConfig], ModelClient]


def build_provider_agent(
    *,
    name: str,
    expected_provider: str,
    model_config: ModelConfig,
    agent_options: dict[str, Any] | None = None,
    client_factory: ClientFactory | None = None,
) -> ProviderAgent:
    if model_config.provider != expected_provider:
        raise ValueError(
            f"Agent '{name}' expects model.provider='{expected_provider}', "
            f"got '{model_config.provider}'."
        )
    factory = client_factory or _client_factory(expected_provider)
    return ProviderAgent(
        name=name,
        client=factory(model_config),
        options=dict(agent_options or {}),
    )


def provider_agent_status(provider: str) -> tuple[bool, str]:
    checks = {
        "openai": ("openai", "OPENAI_API_KEY"),
        "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
        "gemini": ("google.genai", "GEMINI_API_KEY or GOOGLE_API_KEY"),
        "vllm": ("httpx", "VLLM_BASE_URL or model.options.base_url"),
    }
    module_name, key_label = checks[provider]
    if provider == "anthropic":
        try:
            __import__("anthropic")
        except Exception:
            try:
                __import__("httpx")
            except Exception as exc:
                return False, f"anthropic SDK missing and httpx import failed: {type(exc).__name__}: {exc}"
    else:
        try:
            __import__(module_name)
        except Exception as exc:
            return False, f"{provider} SDK import failed: {type(exc).__name__}: {exc}"

    if provider == "vllm":
        base_url = os.environ.get("VLLM_BASE_URL")
        if not base_url:
            return True, "httpx import succeeded. Set model.options.base_url or VLLM_BASE_URL at runtime."
        return True, "httpx import and VLLM_BASE_URL check succeeded"
    if provider == "gemini":
        has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    elif provider == "openai":
        has_key = bool(_openai_api_key({}))
    elif provider == "anthropic":
        has_key = bool(_anthropic_api_key({}))
    else:
        has_key = bool(os.environ.get(key_label))
    if not has_key:
        if provider == "openai":
            return False, "OPENAI_API_KEY is not set and Codex OpenAI auth was not found"
        if provider == "anthropic":
            return False, "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is not set"
        return False, f"{key_label} is not set"
    if provider == "anthropic":
        return True, "anthropic SDK/httpx fallback and API key check succeeded"
    return True, f"{provider} SDK import and API key check succeeded"


def _client_factory(provider: str) -> ClientFactory:
    if provider == "openai":
        return lambda config: OpenAIModelClient(model=config.name, options=config.options)
    if provider == "anthropic":
        return lambda config: AnthropicModelClient(model=config.name, options=config.options)
    if provider == "gemini":
        return lambda config: GeminiModelClient(model=config.name, options=config.options)
    if provider == "vllm":
        return lambda config: VLLMModelClient(model=config.name, options=config.options)
    raise ValueError(f"Unsupported model provider: {provider}")


OPENAI_CLIENT_OPTION_KEYS = {
    "api_key_env",
    "base_url",
    "base_url_env",
    "base_url_suffix",
    "codex_auth_path",
    "codex_config_path",
    "codex_provider",
    "developer_message",
    "empty_response_retries",
    "enterpriseops_provider",
    "llm_provider",
    "request_retries",
    "retry_backoff_seconds",
    "use_codex_auth",
    "use_codex_config",
}

ANTHROPIC_CLIENT_OPTION_KEYS = {
    "api_key",
    "api_key_env",
    "auth_token_env",
    "base_url",
    "anthropic_version",
    "timeout",
}

VLLM_CLIENT_OPTION_KEYS = {
    "api_key",
    "api_key_env",
    "base_url",
    "base_url_env",
    "request_retries",
    "retry_backoff_seconds",
    "timeout",
}


def _openai_client_options(options: dict[str, Any]) -> dict[str, Any]:
    client_options: dict[str, Any] = {"api_key": _openai_api_key(options)}
    base_url = _openai_base_url(options)
    if base_url:
        client_options["base_url"] = base_url
    return client_options


def _openai_request_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in OPENAI_CLIENT_OPTION_KEYS}


def _openai_api_key(options: dict[str, Any]) -> str | None:
    env_name = str(options.get("api_key_env") or "OPENAI_API_KEY")
    value = os.environ.get(env_name)
    if value:
        return value
    if options.get("use_codex_auth", True) is False:
        return None
    auth = _load_json(_codex_auth_path(options))
    value = auth.get("OPENAI_API_KEY")
    return str(value) if isinstance(value, str) and value else None


def _openai_base_url(options: dict[str, Any]) -> str | None:
    value = options.get("base_url")
    if not value and options.get("base_url_env"):
        value = os.environ.get(str(options["base_url_env"]))
    if not value:
        value = os.environ.get("OPENAI_BASE_URL")
    if value:
        base_url = str(value).rstrip("/")
        suffix = options.get("base_url_suffix")
        if suffix:
            base_url = f"{base_url}/{str(suffix).lstrip('/')}"
        return base_url
    if options.get("use_codex_config", True) is False:
        return None
    config = _load_toml(_codex_config_path(options))
    provider_name = str(options.get("codex_provider") or config.get("model_provider") or "")
    providers = config.get("model_providers")
    if not isinstance(providers, dict) or not provider_name:
        return None
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return None
    base_url = provider.get("base_url")
    return str(base_url) if isinstance(base_url, str) and base_url else None


def _codex_auth_path(options: dict[str, Any]) -> Path:
    return Path(str(options.get("codex_auth_path") or Path.home() / ".codex" / "auth.json"))


def _codex_config_path(options: dict[str, Any]) -> Path:
    return Path(str(options.get("codex_config_path") or Path.home() / ".codex" / "config.toml"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _anthropic_api_key(options: dict[str, Any]) -> str | None:
    explicit = options.get("api_key")
    if explicit:
        return str(explicit)
    env_names = [
        str(options.get("api_key_env") or "ANTHROPIC_API_KEY"),
        str(options.get("auth_token_env") or "ANTHROPIC_AUTH_TOKEN"),
    ]
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def _anthropic_base_url(options: dict[str, Any], *, default: str | None = "https://api.anthropic.com") -> str | None:
    value = options.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL") or default
    return str(value).rstrip("/") if value else None


def _anthropic_request_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in ANTHROPIC_CLIENT_OPTION_KEYS}


def _vllm_base_url(options: dict[str, Any]) -> str:
    value = options.get("base_url")
    if not value and options.get("base_url_env"):
        value = os.environ.get(str(options["base_url_env"]))
    if not value:
        value = os.environ.get("VLLM_BASE_URL")
    if not value:
        raise RuntimeError("vLLM base URL is not configured. Set model.options.base_url or VLLM_BASE_URL.")
    return str(value).rstrip("/").removesuffix("/v1") + "/v1"


def _vllm_api_key(options: dict[str, Any]) -> str:
    value = options.get("api_key")
    if not value and options.get("api_key_env"):
        value = os.environ.get(str(options["api_key_env"]))
    if not value:
        value = os.environ.get("VLLM_API_KEY")
    return str(value or "nokey")


def _vllm_request_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in VLLM_CLIENT_OPTION_KEYS}


def _anthropic_messages_create(
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    options: dict[str, Any],
    client_options: dict[str, Any],
) -> dict[str, Any]:
    import httpx

    api_key = _anthropic_api_key(client_options)
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is not set")
    base_url = _anthropic_base_url(client_options)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        **options,
    }
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": str(client_options.get("anthropic_version") or "2023-06-01"),
    }
    with httpx.Client(timeout=float(client_options.get("timeout", 120.0))) as client:
        response = client.post(f"{base_url}/v1/messages", headers=headers, json=payload)
        if response.status_code in {401, 403} or any(
            marker in response.text.lower()
            for marker in ("insufficient_quota", "quota", "credit balance", "billing", "permission denied")
        ):
            raise FatalRunError(f"fatal Anthropic API response {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()


def _find_bridge(environment: Any, options: dict[str, Any]) -> Callable[..., Any] | None:
    bridge = options.get("bridge")
    if callable(bridge):
        return bridge
    if isinstance(bridge, str):
        candidate = getattr(environment, bridge, None)
        if callable(candidate):
            return candidate
    for name in ("run_recovery_bench_agent", "run_with_model", "run_agent"):
        candidate = getattr(environment, name, None)
        if callable(candidate):
            return candidate
    return None


def _available_bridge_methods(environment: Any) -> list[str]:
    names = []
    for name in ("run_recovery_bench_agent", "run_with_model", "run_agent"):
        if callable(getattr(environment, name, None)):
            names.append(name)
    return names


def _call_bridge(
    bridge: Callable[..., Any],
    *,
    task: Task,
    prompt: str,
    environment: Any,
    context: AgentContext,
    client: ModelClient,
    agent_options: dict[str, Any],
) -> Any:
    kwargs = {
        "task": task,
        "prompt": prompt,
        "environment": environment,
        "context": context,
        "model_client": client,
        "client": client,
        "options": dict(agent_options),
    }
    try:
        call_kwargs = _bridge_kwargs(bridge, kwargs)
    except (TypeError, ValueError):
        return bridge(task, prompt, client, context)
    if call_kwargs is None:
        return bridge(task, prompt, client, context)
    return bridge(**call_kwargs)


def _bridge_kwargs(
    bridge: Callable[..., Any],
    candidates: dict[str, Any],
) -> dict[str, Any] | None:
    params = signature(bridge).parameters
    if not params:
        return {}
    if any(param.kind is Parameter.VAR_KEYWORD for param in params.values()):
        return candidates
    if any(param.kind is Parameter.POSITIONAL_ONLY for param in params.values()):
        return None
    keyword_params = {
        name
        for name, param in params.items()
        if param.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in candidates.items() if key in keyword_params}


def _normalize_agent_result(
    result: Any,
    *,
    agent_name: str,
    provider: str,
    model: str,
    bridge: str,
) -> AgentRunResult:
    if isinstance(result, AgentRunResult):
        metadata = {
            **result.metadata,
            "agent": agent_name,
            "provider": provider,
            "model": model,
            "bridge_method": bridge,
        }
        metadata.setdefault("bridge", bridge)
        return AgentRunResult(actions=result.actions, metadata=metadata, error=result.error)

    if isinstance(result, ModelResponse):
        return AgentRunResult(
            actions=(ActionRecord(action="model_response", observation=result.text, metadata=result.metadata),),
            metadata={
                "agent": agent_name,
                "provider": provider,
                "model": model,
                "bridge": bridge,
                "bridge_method": bridge,
            },
        )

    if isinstance(result, str):
        return AgentRunResult(
            actions=(ActionRecord(action="text_response", observation=result),),
            metadata={
                "agent": agent_name,
                "provider": provider,
                "model": model,
                "bridge": bridge,
                "bridge_method": bridge,
            },
        )

    if isinstance(result, dict):
        return AgentRunResult(
            actions=(ActionRecord(action="bridge_result", observation=result),),
            metadata={
                "agent": agent_name,
                "provider": provider,
                "model": model,
                "bridge": bridge,
                "bridge_method": bridge,
            },
        )

    return AgentRunResult(
        actions=(ActionRecord(action="bridge_result", observation=_safe_str(result)),),
        metadata={
            "agent": agent_name,
            "provider": provider,
            "model": model,
            "bridge": bridge,
            "bridge_method": bridge,
        },
    )


def _extract_openai_text(response: Any) -> str:
    output = getattr(response, "output", None)
    if not output:
        return _safe_str(response)
    parts: list[str] = []
    for item in output:
        content = getattr(item, "content", None)
        if not content:
            continue
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts) if parts else _safe_str(response)


def _is_retryable_openai_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429, 500, 502, 503, 504, 524}:
        return True
    message = str(exc)
    return any(f" {code} " in message or f": {code}" in message for code in (408, 409, 429, 500, 502, 503, 504, 524))


def _openai_empty_retry_options(options: dict[str, Any]) -> dict[str, Any]:
    retry_options = dict(options)
    retry_instruction = (
        "Your previous response contained no visible text. "
        "Return exactly one visible JSON object or text response now."
    )
    input_value = retry_options.get("input")
    if isinstance(input_value, list):
        retry_options["input"] = [
            *input_value,
            {"role": "user", "content": retry_instruction},
        ]
    else:
        retry_options["input"] = f"{input_value or ''}\n\n{retry_instruction}".strip()
    return retry_options


def _extract_anthropic_text(response: Any) -> str:
    if isinstance(response, dict):
        parts = []
        for block in response.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts) if parts else _safe_str(response)
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts) if parts else _safe_str(response)


def _extract_chat_completion_text(response: Any) -> str:
    if not isinstance(response, dict):
        return _safe_str(response)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return _safe_str(response)
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return _safe_str(response)
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return _safe_str(response)


def _normalize_gemini_options(options: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(options)
    if "config" in normalized:
        return normalized
    config_keys = {
        "candidate_count",
        "max_output_tokens",
        "response_mime_type",
        "response_schema",
        "safety_settings",
        "seed",
        "stop_sequences",
        "system_instruction",
        "temperature",
        "tool_config",
        "tools",
        "top_k",
        "top_p",
    }
    config = {key: normalized.pop(key) for key in list(normalized) if key in config_keys}
    if config:
        normalized["config"] = config
    return normalized


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return repr(value)


def _callable_name(func: Callable[..., Any]) -> str:
    return getattr(func, "__qualname__", getattr(func, "__name__", repr(func)))
