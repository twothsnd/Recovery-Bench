from __future__ import annotations

import json
import sys
import types
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from recovery_bench.adapters import appworld as appworld_adapter
from recovery_bench.adapters.appworld import AppWorldAgentEnvironment
from recovery_bench.adapters.enterpriseops_gym import (
    EnterpriseOpsGymAgentEnvironment,
    EnterpriseOpsGymBenchmarkAdapter,
    _attach_runtime_database_ids,
    _compact_tools,
    _extract_enterpriseops_actions,
    _official_gym_config_without_database_id,
    _official_user_prompt,
    _patch_official_llm_endpoint,
)
from recovery_bench.adapters import osworld as osworld_adapter
from recovery_bench.adapters.osworld import OSWorldBenchmarkAdapter, _extract_osworld_actions
from recovery_bench.adapters.tau_bench import (
    TauBenchAgentEnvironment,
    _official_tau_task_for_attempt,
    _official_tau_task_prompt,
    _prepare_tau_env_kwargs,
)
from recovery_bench.agents.provider import ModelResponse, ProviderAgent
from recovery_bench.errors import FatalRunError
from recovery_bench.types import AgentContext, StateSnapshot, Task


@dataclass(slots=True)
class SequenceClient:
    outputs: list[str]
    provider: str = "fake"
    model: str = "fake-model"
    prompts: list[str] = field(default_factory=list)

    def complete(self, prompt: str, *, context: AgentContext) -> ModelResponse:
        self.prompts.append(prompt)
        return ModelResponse(text=self.outputs.pop(0), metadata={"task_id": context.task_id})


class FakeAppWorld:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, code: str) -> str:
        self.executed.append(code)
        return "done" if "complete_task" in code else "not done"

    def task_completed(self) -> bool:
        return any("complete_task" in code for code in self.executed)


class FakeSupervisorTask:
    _tasks: list["FakeSupervisorTask"] = []

    def __init__(self, *, status: str | None = "success", answer: str = '"42"') -> None:
        self.status = status
        self.answer = answer
        self.saved = False

    @classmethod
    def all(cls) -> list["FakeSupervisorTask"]:
        return cls._tasks

    def save(self) -> None:
        self.saved = True


class FakeTauEnv:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def step(self, action: str):
        self.actions.append(action)
        terminated = len(self.actions) >= 2
        return f"obs-{len(self.actions)}", 1.0 if terminated else 0.0, terminated, False, {}


@dataclass
class FakeOfficialTauTask:
    initial_state: Any

    def model_copy(self, *, deep: bool = False):
        return deepcopy(self) if deep else FakeOfficialTauTask(initial_state=self.initial_state)


@dataclass
class FakeTauSimulationRun:
    messages: list[Any]

    def get_messages(self) -> list[Any]:
        return self.messages


class FakeEnterpriseOpsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        return {"success": True, "result": {"ok": True}, "error": None}


class FakeOSWorldDesktopEnv:
    instances: list["FakeOSWorldDesktopEnv"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.provider_name = kwargs.get("provider_name", "docker")
        self.snapshot_name = "init_state"
        self.reset_count = 0
        self.save_state_calls: list[str] = []
        self.revert_calls: list[str] = []
        self.start_count = 0
        self.action_history: list[Any] = []
        self.evaluation_side_effect = False
        self.restored_checkpoint = False
        self.closed = False
        FakeOSWorldDesktopEnv.instances.append(self)

    def reset(self, task_config: dict[str, Any]):
        self.reset_count += 1
        self.task_config = task_config
        self.action_history.clear()
        self.evaluation_side_effect = False
        self.restored_checkpoint = False
        return {"screenshot": b"png", "accessibility_tree": None, "instruction": task_config["instruction"]}

    def step(self, action: Any, pause: float = 0.0):
        self.action_history.append(action)
        return {"screenshot": b"png2", "accessibility_tree": None, "instruction": "do it"}, 0, action == "DONE", {}

    def evaluate(self) -> float:
        self.evaluation_side_effect = bool(self.task_config.get("evaluator", {}).get("postconfig", []))
        return 1.0 if "DONE" in self.action_history else 0.0

    def _save_state(self, snapshot_name: str) -> None:
        self.save_state_calls.append(snapshot_name)

    def _revert_to_snapshot(self) -> None:
        self.revert_calls.append(self.snapshot_name)
        self.evaluation_side_effect = False
        self.restored_checkpoint = True

    def _start_emulator(self) -> None:
        self.start_count += 1

    def _get_obs(self):
        return {"screenshot": b"png3", "accessibility_tree": None, "instruction": "restored"}

    def close(self) -> None:
        self.closed = True


def _context() -> AgentContext:
    return AgentContext(
        benchmark="bench",
        task_id="task-1",
        protocol="recovery",
        attempt_index=1,
        k=3,
    )


def test_osworld_extracts_fenced_pyautogui_action() -> None:
    assert _extract_osworld_actions("```python\nimport pyautogui\npyautogui.click(1, 2)\n```") == [
        "import pyautogui\npyautogui.click(1, 2)"
    ]
    assert _extract_osworld_actions('{"actions": ["DONE"]}') == []
    assert _extract_osworld_actions("DONE") == ["DONE"]


def test_osworld_rejects_reasoning_as_action() -> None:
    response = (
        "The next step is to click the profile icon. I can use pyautogui.click(x=10, y=20), "
        "but first I should inspect the screen."
    )

    assert _extract_osworld_actions(response) == []


def test_osworld_rejects_action_prefixed_statement() -> None:
    response = "Action: pyautogui.click(x=10, y=20)"

    assert _extract_osworld_actions(response) == []


def test_osworld_rejects_mixed_reasoning_and_action() -> None:
    response = (
        "Action: Click on the profile icon.\n"
        "</think>\n\n"
        "pyautogui.click(x=892, y=83)"
    )

    assert _extract_osworld_actions(response) == []


def test_osworld_rejects_unclosed_fenced_action() -> None:
    response = "```python\nimport pyautogui\npyautogui.click(x=10, y=20)\n"

    assert _extract_osworld_actions(response) == []


def test_osworld_passes_fenced_code_to_environment_without_rewriting() -> None:
    response = "```python\npyautogui.click(x=10, y=\n```"

    assert _extract_osworld_actions(response) == ["pyautogui.click(x=10, y="]


def test_osworld_restore_preserves_live_dirty_state(monkeypatch, tmp_path) -> None:
    source = tmp_path / "osworld"
    examples = source / "evaluation_examples" / "examples" / "chrome"
    examples.mkdir(parents=True)
    (source / "evaluation_examples" / "test_all.json").write_text(
        json.dumps({"chrome": ["task-1"]}),
        encoding="utf-8",
    )
    (examples / "task-1.json").write_text(
        json.dumps(
            {
                "id": "task-1",
                "instruction": "do it",
                "config": [],
                "evaluator": {"func": "exact_match", "result": [], "postconfig": [{"type": "noop"}]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(osworld_adapter, "_desktop_env_class", lambda: FakeOSWorldDesktopEnv)
    FakeOSWorldDesktopEnv.instances.clear()
    adapter = OSWorldBenchmarkAdapter(
        source_path=source,
        options={"provider_name": "docker", "observation_type": "screenshot", "osworld_restore_strategy": "live"},
    )
    task = adapter.load_task("task-1")

    adapter.reset(task)
    env = adapter.agent_environment()
    client = SequenceClient(outputs=["```python\nimport pyautogui\npyautogui.click(1, 2)\n```"])
    result = env.run_recovery_bench_agent(
        prompt="solve",
        model_client=client,
        context=_context(),
        options={"max_steps": 1},
    )
    dirty = adapter.snapshot(label="dirty")
    restored = adapter.restore(dirty)

    desktop = FakeOSWorldDesktopEnv.instances[0]
    assert result.error is None
    assert desktop.reset_count == 1
    assert desktop.action_history == ["import pyautogui\npyautogui.click(1, 2)"]
    assert restored.payload["restore_strategy"] == "live-osworld-env"
    assert restored.payload["action_history_length"] == 1

    adapter.reset(task)

    assert desktop.reset_count == 2
    assert desktop.action_history == []


def test_osworld_strict_checkpoint_rejects_docker_provider(monkeypatch, tmp_path) -> None:
    source = tmp_path / "osworld"
    examples = source / "evaluation_examples" / "examples" / "chrome"
    examples.mkdir(parents=True)
    (source / "evaluation_examples" / "test_all.json").write_text(json.dumps({"chrome": ["task-1"]}), encoding="utf-8")
    (examples / "task-1.json").write_text(
        json.dumps({"id": "task-1", "instruction": "do it", "config": [], "evaluator": {"func": "exact_match"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(osworld_adapter, "_desktop_env_class", lambda: FakeOSWorldDesktopEnv)
    FakeOSWorldDesktopEnv.instances.clear()
    adapter = OSWorldBenchmarkAdapter(source_path=source, options={"provider_name": "docker"})
    task = adapter.load_task("task-1")

    with pytest.raises(RuntimeError, match="Docker provider does not support snapshots"):
        adapter.reset(task)


def test_osworld_checkpoint_restore_uses_pre_evaluate_provider_snapshot(monkeypatch, tmp_path) -> None:
    source = tmp_path / "osworld"
    examples = source / "evaluation_examples" / "examples" / "chrome"
    examples.mkdir(parents=True)
    (source / "evaluation_examples" / "test_all.json").write_text(json.dumps({"chrome": ["task-1"]}), encoding="utf-8")
    (examples / "task-1.json").write_text(
        json.dumps(
            {
                "id": "task-1",
                "instruction": "do it",
                "config": [],
                "evaluator": {"func": "exact_match", "result": [], "postconfig": [{"type": "execute"}]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(osworld_adapter, "_desktop_env_class", lambda: FakeOSWorldDesktopEnv)
    FakeOSWorldDesktopEnv.instances.clear()
    adapter = OSWorldBenchmarkAdapter(
        source_path=source,
        options={"provider_name": "vmware", "observation_type": "screenshot"},
    )
    task = adapter.load_task("task-1")

    adapter.reset(task)
    env = adapter.agent_environment()
    result = env.run_recovery_bench_agent(
        prompt="solve",
        model_client=SequenceClient(outputs=["```python\nimport pyautogui\npyautogui.click(1, 2)\n```"]),
        context=_context(),
        options={"max_steps": 1},
    )
    checkpoint = adapter.snapshot(label="attempt-1-after")
    outcome = adapter.evaluate(task)
    assert result.error is None
    assert outcome.success is False

    desktop = FakeOSWorldDesktopEnv.instances[0]
    assert checkpoint.payload["restore_strategy"] == "provider-checkpoint"
    assert checkpoint.payload["strict_recovery"] is True
    assert len(desktop.save_state_calls) == 1
    assert desktop.evaluation_side_effect is True

    restored = adapter.restore(checkpoint)

    assert desktop.revert_calls == [checkpoint.payload["checkpoint_name"]]
    assert desktop.start_count == 1
    assert desktop.evaluation_side_effect is False
    assert desktop.restored_checkpoint is True
    assert desktop.action_history == ["import pyautogui\npyautogui.click(1, 2)"]
    assert restored.payload["restore_strategy"] == "live-osworld-env"


def test_appworld_bridge_executes_model_python_code() -> None:
    world = FakeAppWorld()
    env = AppWorldAgentEnvironment(world=world)
    client = SequenceClient(outputs=["```python\napis.supervisor.complete_task()\n```"])
    agent = ProviderAgent(name="fake-agent", client=client, options={"max_steps": 3})

    result = agent.run(Task(task_id="task-1", prompt="do it"), "solve", env, _context())

    assert result.error is None
    assert world.executed == ["apis.supervisor.complete_task()"]
    assert result.metadata["bridge"] == "appworld"
    assert result.metadata["bridge_method"] == "AppWorldAgentEnvironment.run_recovery_bench_agent"
    assert result.metadata["steps"] == 1


class DummyFreezer:
    def __init__(self) -> None:
        self._freezer = object()


class DummyRequester:
    time_freezers_or_ids: list = []
    time_freezer_id_to_remote_apis_url: dict = {}

    def __init__(self, freezer: DummyFreezer) -> None:
        self.time_freezer_or_id = freezer


class DummyApis:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("apis")


class DummyAppWorld:
    id_to_time_freezer: dict[str, DummyFreezer] = {}

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.remote_environment_url = None
        self.remote_apis_url = None
        self.temporary_output_db_home_path_on_disk = None
        self.task_id = "task-1"
        self.time_freezer_id = "freezer-1"
        self.time_freezer = DummyFreezer()
        self.requester = DummyRequester(DummyFreezer())
        self.apis = DummyApis(events)

    def _unset_datetime(self) -> None:
        self.events.append("unset")


def test_appworld_safe_close_unsets_active_world_freezer_before_apis(monkeypatch) -> None:
    monkeypatch.setattr(appworld_adapter, "_clear_appworld_db_cache", lambda world: None)
    monkeypatch.setattr(appworld_adapter, "_reset_appworld_gc_threshold", lambda: None)
    events: list[str] = []
    world = DummyAppWorld(events)
    DummyAppWorld.id_to_time_freezer = {world.time_freezer_id: world.time_freezer}

    appworld_adapter._close_appworld_world_safely(world)

    assert events == ["unset", "apis"]
    assert DummyAppWorld.id_to_time_freezer == {}


def test_appworld_safe_close_skips_stale_world_freezer_after_restore(monkeypatch) -> None:
    monkeypatch.setattr(appworld_adapter, "_clear_appworld_db_cache", lambda world: None)
    monkeypatch.setattr(appworld_adapter, "_reset_appworld_gc_threshold", lambda: None)
    events: list[str] = []
    world = DummyAppWorld(events)
    DummyAppWorld.id_to_time_freezer = {}

    appworld_adapter._close_appworld_world_safely(world)

    assert events == ["apis"]
    assert world.time_freezer._freezer is None


def test_appworld_language_model_guard_raises_fatal_permission_errors() -> None:
    class FakeLanguageModel:
        def lm_call(self, **_: Any) -> None:
            raise RuntimeError("Access denied. Insufficient permissions.")

    class FakeOfficialAgent:
        language_model = FakeLanguageModel()

    appworld_adapter._guard_appworld_language_model_fatal_errors(FakeOfficialAgent())

    with pytest.raises(FatalRunError):
        FakeOfficialAgent.language_model.lm_call()


def test_appworld_restore_clears_only_completion_marker(monkeypatch) -> None:
    task = FakeSupervisorTask(status="success", answer='"wrong"')
    FakeSupervisorTask._tasks = [task]
    constants_module = types.ModuleType("appworld.apps.supervisor.constants")
    constants_module.NOT_GIVEN_ANSWER = '"<<NOT_GIVEN>>"'
    models_module = types.ModuleType("appworld.apps.supervisor.models")
    models_module.Task = FakeSupervisorTask
    monkeypatch.setitem(sys.modules, "appworld.apps.supervisor.constants", constants_module)
    monkeypatch.setitem(sys.modules, "appworld.apps.supervisor.models", models_module)

    changed = appworld_adapter._clear_appworld_completion_marker(object())

    assert changed is True
    assert task.status is None
    assert task.answer == '"wrong"'
    assert task.saved is True


def test_tau_bridge_steps_gym_environment_until_done() -> None:
    tau_env = FakeTauEnv()
    env = TauBenchAgentEnvironment(env=tau_env, task=Task(task_id="task-1", prompt="do it"), reset_observation="obs-0")
    client = SequenceClient(outputs=["Action: greet user", "```text\ncall tool\n```"])
    agent = ProviderAgent(name="fake-agent", client=client, options={"max_steps": 5})

    result = agent.run(Task(task_id="task-1", prompt="do it"), "solve", env, _context())

    assert result.error is None
    assert tau_env.actions == ["greet user", "call tool"]
    assert result.metadata["bridge"] == "tau-bench-gym"
    assert result.metadata["bridge_method"] == "TauBenchAgentEnvironment.run_recovery_bench_agent"
    assert result.metadata["steps"] == 2
    assert result.metadata["terminated"] is True


def test_tau_official_task_prompt_does_not_disclose_hidden_task_details() -> None:
    prompt = _official_tau_task_prompt("airline", "0")

    assert "user scenario" not in prompt.lower()
    assert "evaluation" not in prompt.lower()
    assert "policy" not in prompt.lower()
    assert "standard user simulator" in prompt


def test_tau_official_recovery_replays_previous_message_history_before_prompt() -> None:
    message_module = pytest.importorskip("tau2.data_model.message")
    tasks_module = pytest.importorskip("tau2.data_model.tasks")
    UserMessage = message_module.UserMessage
    InitialState = tasks_module.InitialState
    original_message = UserMessage(role="user", content="clean initial message", cost=0.0)
    previous_message = UserMessage(role="user", content="previous failed attempt message", cost=0.0)
    fake_task = FakeOfficialTauTask(initial_state=InitialState(message_history=[original_message]))
    previous_run = FakeTauSimulationRun(messages=[previous_message])

    task_for_attempt = _official_tau_task_for_attempt(
        fake_task,
        start_simulation_run=previous_run,
        recovery_prompt="Your previous attempt failed.",
    )

    contents = [message.content for message in task_for_attempt.initial_state.message_history]
    assert contents == ["previous failed attempt message", "Your previous attempt failed."]


def test_tau_env_kwargs_fill_user_llm_from_codex_openai_config(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    auth_path.rename(codex_dir / "auth.json")
    config_path.rename(codex_dir / "config.toml")

    kwargs = _prepare_tau_env_kwargs({"user_llm": "openai/gpt-5.5"})

    assert kwargs["user_llm_args"]["api_key"] == "sk-test"
    assert kwargs["user_llm_args"]["api_base"] == "https://example.test/v1"
    assert kwargs["user_llm_args"]["base_url"] == "https://example.test/v1"


def test_tau_env_kwargs_fill_anthropic_user_llm(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic.example.test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    kwargs = _prepare_tau_env_kwargs({"user_llm": "anthropic/claude-sonnet-4-5"})

    assert kwargs["user_llm_args"]["api_key"] == "sk-ant-test"
    assert kwargs["user_llm_args"]["api_base"] == "https://anthropic.example.test"
    assert kwargs["user_llm_args"]["base_url"] == "https://anthropic.example.test"
    assert kwargs["user_llm"] == "anthropic/claude-sonnet-4-5"


def test_tau_env_kwargs_treat_openai_prefixed_claude_as_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-compat")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-compatible.example.test/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic.example.test")

    kwargs = _prepare_tau_env_kwargs({"user_llm": "openai/claude-sonnet-4-5"})

    assert kwargs["user_llm_args"]["api_key"] == "sk-openai-compat"
    assert kwargs["user_llm_args"]["api_base"] == "https://openai-compatible.example.test/v1"
    assert kwargs["user_llm_args"]["base_url"] == "https://openai-compatible.example.test/v1"
    assert kwargs["user_llm"] == "openai/claude-sonnet-4-5"


def test_enterpriseops_bridge_executes_json_tool_calls_and_final_answer() -> None:
    fake_client = FakeEnterpriseOpsClient()
    state = {}
    env = EnterpriseOpsGymAgentEnvironment(
        config={"system_prompt": "Use tools.", "user_prompt": "do it"},
        clients={"gym": fake_client},
        available_tools=[{"name": "create_ticket", "description": "Create ticket", "inputSchema": {"type": "object"}}],
        tool_to_gym={"create_ticket": "gym"},
        state_sink=lambda result, model_client: state.update({"result": result, "model_client": model_client}),
    )
    client = SequenceClient(
        outputs=[
            '{"tool_name": "create_ticket", "arguments": {"title": "A"}}',
            '{"final_answer": "done"}',
        ]
    )
    agent = ProviderAgent(name="fake-agent", client=client, options={"max_steps": 3})

    result = agent.run(Task(task_id="task-1", prompt="do it"), "solve", env, _context())

    assert result.error is None
    assert fake_client.calls == [("create_ticket", {"title": "A"})]
    assert result.metadata["bridge"] == "enterpriseops-gym-json-mcp"
    assert result.metadata["bridge_method"] == "EnterpriseOpsGymAgentEnvironment.run_recovery_bench_agent"
    assert result.metadata["steps"] == 2
    assert state["result"]["final_response"] == "done"


def test_enterpriseops_official_prompt_uses_original_user_prompt_for_first_attempt() -> None:
    task = Task(task_id="task-1", prompt="official user prompt")
    context = AgentContext(benchmark="enterpriseops-gym", task_id="task-1", protocol="success", attempt_index=1, k=1)

    prompt = _official_user_prompt(
        {"user_prompt": "official user prompt"},
        task=task,
        prompt="Complete the original task.\n\nOriginal task:\nofficial user prompt\n",
        context=context,
    )

    assert prompt == "official user prompt"


def test_enterpriseops_official_prompt_uses_recovery_prompt_after_failure() -> None:
    task = Task(task_id="task-1", prompt="official user prompt")
    context = AgentContext(benchmark="enterpriseops-gym", task_id="task-1", protocol="recovery", attempt_index=2, k=3)
    recovery_prompt = "Your previous attempt failed.\n\nOriginal task:\nofficial user prompt"

    prompt = _official_user_prompt({"user_prompt": "official user prompt"}, task=task, prompt=recovery_prompt, context=context)

    assert prompt == recovery_prompt


def test_enterpriseops_official_tool_discovery_config_excludes_runtime_database_id() -> None:
    gym = {
        "mcp_server_name": "gym-teams-mcp",
        "mcp_server_url": "http://localhost:8002",
        "seed_database_file": "/tmp/seed.sql",
        "database_id": "runtime-db",
        "mcp_endpoint": "/mcp",
    }

    config = _official_gym_config_without_database_id(gym)

    assert "database_id" not in config
    assert config["mcp_server_name"] == "gym-teams-mcp"


def test_enterpriseops_official_runtime_database_id_is_attached_to_clients_and_configs() -> None:
    class Client:
        database_id = ""

    class Executor:
        gym_configs = [
            {"mcp_server_name": "gym-teams-mcp", "database_id": ""},
            {"mcp_server_name": "gym-calendar", "database_id": ""},
        ]
        mcp_clients = {"gym-teams-mcp": Client(), "gym-calendar": Client()}

    runtime_by_name = {
        "gym-teams-mcp": {"database_id": "dirty-teams-db"},
        "gym-calendar": {"database_id": "dirty-calendar-db"},
    }

    _attach_runtime_database_ids(Executor, runtime_by_name)

    assert Executor.gym_configs == [
        {"mcp_server_name": "gym-teams-mcp", "database_id": "dirty-teams-db"},
        {"mcp_server_name": "gym-calendar", "database_id": "dirty-calendar-db"},
    ]
    assert Executor.mcp_clients["gym-teams-mcp"].database_id == "dirty-teams-db"
    assert Executor.mcp_clients["gym-calendar"].database_id == "dirty-calendar-db"


def test_enterpriseops_patch_passes_base_url_to_official_llm(monkeypatch) -> None:
    anthropic_calls = []
    openai_calls = []

    class FakeChatAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            anthropic_calls.append(kwargs)

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            openai_calls.append(kwargs)

    class FakeLLMClient:
        def __init__(self, provider: str) -> None:
            self.provider = provider
            self.model = "claude-sonnet-4-6"
            self.api_key = "sk-test"
            self.custom_api_endpoint = "https://compatible.example.test"
            self.temperature = 0
            self.max_tokens = 128
            self.top_p = None
            self.llm = None
            self._initialize_llm()

        def _initialize_llm(self) -> None:
            self.llm = "original"

    benchmark_module = types.ModuleType("benchmark")
    llm_client_module = types.ModuleType("benchmark.llm_client")
    llm_client_module.LLMClient = FakeLLMClient
    benchmark_module.llm_client = llm_client_module
    langchain_anthropic = types.ModuleType("langchain_anthropic")
    langchain_anthropic.ChatAnthropic = FakeChatAnthropic
    langchain_openai = types.ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = FakeChatOpenAI

    monkeypatch.setitem(sys.modules, "benchmark", benchmark_module)
    monkeypatch.setitem(sys.modules, "benchmark.llm_client", llm_client_module)
    monkeypatch.setitem(sys.modules, "langchain_anthropic", langchain_anthropic)
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)

    with _patch_official_llm_endpoint(None):
        patched_anthropic = FakeLLMClient("anthropic")
        patched_openai = FakeLLMClient("openai")

    assert anthropic_calls == [
        {
            "model": "claude-sonnet-4-6",
            "anthropic_api_key": "sk-test",
            "base_url": "https://compatible.example.test",
            "temperature": 0,
            "max_tokens": 128,
        }
    ]
    assert openai_calls == [
        {
            "model": "claude-sonnet-4-6",
            "openai_api_key": "sk-test",
            "openai_api_base": "https://compatible.example.test",
            "temperature": 0,
            "max_tokens": 128,
        }
    ]
    assert isinstance(patched_anthropic.llm, FakeChatAnthropic)
    assert isinstance(patched_openai.llm, FakeChatOpenAI)
    assert FakeLLMClient("openai").llm == "original"


def test_enterpriseops_official_restore_does_not_run_custom_mcp_client(monkeypatch) -> None:
    calls = []

    def fake_connect_clients(self) -> None:
        calls.append(self.name)

    monkeypatch.setattr(EnterpriseOpsGymBenchmarkAdapter, "_connect_clients", fake_connect_clients)
    adapter = EnterpriseOpsGymBenchmarkAdapter()
    adapter._current_task = Task(task_id="task-1", prompt="do it")

    adapter.restore(
        StateSnapshot(
            payload={
                "gyms": [
                    {
                        "mcp_server_name": "gym-teams-mcp",
                        "mcp_server_url": "http://localhost:8002",
                        "database_id": "dirty-db",
                    }
                ]
            }
        )
    )

    assert calls == []


def test_enterpriseops_legacy_restore_still_reconnects_custom_mcp_client(monkeypatch) -> None:
    calls = []

    def fake_connect_clients(self) -> None:
        calls.append(self.name)

    monkeypatch.setattr(EnterpriseOpsGymBenchmarkAdapter, "_connect_clients", fake_connect_clients)
    adapter = EnterpriseOpsGymBenchmarkAdapter(options={"execution_mode": "legacy"})
    adapter._current_task = Task(task_id="task-1", prompt="do it")

    adapter.restore(
        StateSnapshot(
            payload={
                "gyms": [
                    {
                        "mcp_server_name": "gym-teams-mcp",
                        "mcp_server_url": "http://localhost:8002",
                        "database_id": "dirty-db",
                    }
                ]
            }
        )
    )

    assert calls == ["enterpriseops-gym"]


def test_enterpriseops_parser_accepts_multiple_json_tool_calls() -> None:
    actions = _extract_enterpriseops_actions(
        '{"tool_name": "list_team_members", "arguments": {"teamId": "team_1"}}\n\n'
        '{"tool_name": "create_chat", "arguments": {"chatType": "oneOnOne"}}'
    )

    assert actions == [
        {"tool_name": "list_team_members", "arguments": {"teamId": "team_1"}},
        {"tool_name": "create_chat", "arguments": {"chatType": "oneOnOne"}},
    ]


def test_enterpriseops_parser_accepts_tool_call_block_and_xml_invoke() -> None:
    actions = _extract_enterpriseops_actions(
        """I'll call the tool.
[TOOL_CALL]
{tool_name => "list_teams", arguments => {"_filter": "displayName eq 'TechCorp Solutions Team'"}}
[/TOOL_CALL]
<invoke name="create_chat"><parameter name="chatType">oneOnOne</parameter></invoke>
"""
    )

    assert actions == [
        {"tool_name": "list_teams", "arguments": {"_filter": "displayName eq 'TechCorp Solutions Team'"}},
        {"tool_name": "create_chat", "arguments": {"chatType": "oneOnOne"}},
    ]


def test_enterpriseops_parser_accepts_tool_call_block_with_single_equals() -> None:
    actions = _extract_enterpriseops_actions(
        """I'll call the tool.
[TOOL_CALL]
{tool_name = "list_teams", arguments = {"_filter": "displayName eq 'TechCorp Solutions Team'"}}
[/TOOL_CALL]
"""
    )

    assert actions == [
        {"tool_name": "list_teams", "arguments": {"_filter": "displayName eq 'TechCorp Solutions Team'"}},
    ]


def test_enterpriseops_compact_tools_preserves_parameter_names_without_return_schema() -> None:
    compact = _compact_tools(
        [
            {
                "name": "create_chat",
                "description": "Create a chat.",
                "inputSchema": {
                    "type": "object",
                    "required": ["chatType"],
                    "properties": {
                        "chatType": {"type": "string", "description": "Type", "enum": ["oneOnOne"]},
                        "members": {"type": "array", "description": "Members", "items": {"type": "string"}},
                    },
                    "returns": {"very": "large"},
                },
            }
        ],
        schema_mode="parameters",
    )

    assert compact == [
        {
            "name": "create_chat",
            "description": "Create a chat.",
            "inputSchema": {
                "type": "object",
                "required": ["chatType"],
                "properties": {
                    "chatType": {"type": "string", "description": "Type", "enum": ["oneOnOne"]},
                    "members": {"type": "array", "description": "Members", "items": {"type": "string"}},
                },
            },
        }
    ]


def test_enterpriseops_config_folder_filters_domain_and_mode(tmp_path) -> None:
    root = tmp_path / "tasks"
    oracle_teams = root / "oracle" / "teams"
    plus_teams = root / "plus_5_tools" / "teams"
    oracle_csm = root / "oracle" / "csm"
    oracle_teams.mkdir(parents=True)
    plus_teams.mkdir(parents=True)
    oracle_csm.mkdir(parents=True)

    base = {
        "system_prompt": "system",
        "user_prompt": "user",
        "gym_servers_config": [],
        "verifiers": [],
    }
    (oracle_teams / "team-task.json").write_text(
        json.dumps({**base, "task_id": "team-task", "domain": "teams", "mode": "oracle"}),
        encoding="utf-8",
    )
    (plus_teams / "plus-task.json").write_text(
        json.dumps({**base, "task_id": "plus-task", "domain": "teams", "mode": "plus_5_tools"}),
        encoding="utf-8",
    )
    (oracle_csm / "csm-task.json").write_text(
        json.dumps({**base, "task_id": "csm-task", "domain": "csm", "mode": "oracle"}),
        encoding="utf-8",
    )

    adapter = EnterpriseOpsGymBenchmarkAdapter(configs_folder=root, domain="teams", mode="oracle")

    assert adapter.list_tasks() == ["team-task"]


def test_enterpriseops_mcp_server_url_override_by_domain_and_name() -> None:
    adapter = EnterpriseOpsGymBenchmarkAdapter(
        domain="csm",
        mode="oracle",
        options={
            "mcp_server_url_overrides": {
                "csm": "http://localhost:8011",
                "gym-teams-mcp": "http://localhost:8002",
            }
        },
    )

    csm_config = adapter._task_gym_configs(
        {
            "seed_database_file": "Domain Wise DBs and Task-DB Mappings/csm/dbs/db.sql",
            "mcp_server_url": "http://localhost:8001",
        }
    )
    teams_config = adapter._apply_mcp_server_overrides(
        {
            "mcp_server_name": "gym-teams-mcp",
            "mcp_server_url": "http://localhost:9999",
            "seed_database_file": "Domain Wise DBs and Task-DB Mappings/teams/dbs/db.sql",
        }
    )

    assert csm_config[0]["mcp_server_url"] == "http://localhost:8011"
    assert teams_config["mcp_server_url"] == "http://localhost:8002"
