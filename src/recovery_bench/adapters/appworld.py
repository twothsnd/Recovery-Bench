from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agents.provider import _anthropic_api_key, _anthropic_base_url, _openai_api_key, _openai_base_url
from ..errors import raise_if_fatal_api_error
from ..io import dump_dataclass_json
from ..prompts import prefix_with_previous_attempt_trajectory
from ..types import (
    ActionRecord,
    AgentContext,
    AgentRunResult,
    BenchmarkAdapter,
    BenchmarkCapabilities,
    BenchmarkResult,
    StateSnapshot,
    Task,
    TaskOutcome,
)

_APPWORLD_TESTCLIENT_PATCHED = False
APPWORLD_OFFICIAL_MAX_STEPS = 50


def can_build_appworld_benchmark() -> bool:
    available, _reason = appworld_dependency_status()
    return available


def appworld_dependency_status() -> tuple[bool, str]:
    _add_source_path(_default_source_path())
    try:
        import appworld  # noqa: F401
    except Exception as exc:
        return (
            False,
            "AppWorld import failed. Install appworld or set benchmark.options.source_path "
            f"to a downloaded archive checkout. Import error: {type(exc).__name__}: {exc}",
        )
    return True, "AppWorld package import succeeded."


def build_appworld_benchmark(
    *,
    root: Path | None = None,
    source_path: Path | None = None,
    dataset_name: str = "test_challenge",
    experiment_name: str = "recovery-bench",
    task_ids: tuple[str, ...] = (),
    options: dict[str, Any] | None = None,
) -> "AppWorldBenchmarkAdapter":
    _add_source_path(source_path)
    available, reason = appworld_dependency_status()
    if not available:
        raise NotImplementedError(reason)
    return AppWorldBenchmarkAdapter(
        root=root,
        source_path=source_path,
        dataset_name=dataset_name,
        experiment_name=experiment_name,
        task_ids=task_ids,
        options=options or {},
    )


@dataclass(slots=True)
class AppWorldBenchmarkAdapter(BenchmarkAdapter):
    """AppWorld adapter using official state checkpoints."""

    root: Path | None = None
    source_path: Path | None = None
    dataset_name: str = "test_challenge"
    experiment_name: str = "recovery-bench"
    task_ids: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    name: str = "appworld"
    _world: Any = field(default=None, init=False, repr=False)
    _current_task: Task | None = field(default=None, init=False, repr=False)
    _snapshot_counter: int = field(default=0, init=False, repr=False)

    def load_task(self, task_id: str) -> Task:
        self._configure_root()
        self._import_appworld()
        from appworld.task import Task as AppWorldTask

        task = AppWorldTask.load(
            task_id=task_id,
            load_ground_truth=False,
            include_api_response_schemas=False,
        )
        try:
            instruction = getattr(task, "instruction", None) or str(task)
            metadata = {
                "dataset_name": self.dataset_name,
                "task": self._jsonable_task(task),
            }
            return Task(task_id=task_id, prompt=instruction, metadata=metadata)
        finally:
            task.close()

    def reset(self, task: Task) -> StateSnapshot:
        self._close_world()
        self._configure_root()
        appworld = self._import_appworld()
        self._snapshot_counter = 0
        self._world = appworld.AppWorld(
            task_id=task.task_id,
            experiment_name=self.experiment_name,
            **self._world_kwargs(),
        )
        self._current_task = task
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        world = self._require_world()
        state_id = self._state_id_from_snapshot(snapshot)
        world.load_state(state_id)
        if bool(self.options.get("clear_completion_marker_on_restore", True)):
            _clear_appworld_completion_marker(world)
        return self.snapshot(label=snapshot.label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        world = self._require_world()
        self._snapshot_counter += 1
        safe_label = (label or "snapshot").replace("/", "-").replace(" ", "-")
        state_id = f"recovery-bench-{self._current_task.task_id if self._current_task else 'unknown'}-{safe_label}-{self._snapshot_counter}-{uuid.uuid4().hex}"
        saved_state_id = world.save_state(state_id)
        if saved_state_id is not None:
            state_id = str(saved_state_id)
        return StateSnapshot(
            payload={"state_id": state_id},
            label=label,
            metadata={
                "benchmark": self.name,
                "dataset_name": self.dataset_name,
                "task_id": self._current_task.task_id if self._current_task else None,
                "restore_strategy": "appworld-checkpoint",
            },
        )

    def agent_environment(self) -> Any:
        if self._uses_official_flow():
            return AppWorldOfficialAgentEnvironment(
                world=self._require_world(),
                source_path=self.source_path,
                adapter_options=dict(self.options),
            )
        return AppWorldAgentEnvironment(world=self._require_world(), adapter_options=dict(self.options))

    def evaluate(self, task: Task) -> TaskOutcome:
        world = self._require_world()
        evaluation = world.evaluate().to_dict()
        individual = evaluation.get("individual", {})
        task_eval = individual.get(task.task_id, {}) if isinstance(individual, dict) else {}
        success = bool(task_eval.get("success", evaluation.get("success", False)))
        score = 1.0 if success else 0.0
        aggregate = evaluation.get("aggregate", {})
        if isinstance(aggregate, dict):
            score = aggregate.get("sgc", score)
        return TaskOutcome(success=success, score=score, details=evaluation)

    def list_tasks(self) -> list[str]:
        if self.task_ids:
            return list(self.task_ids)
        self._configure_root()
        appworld = self._import_appworld()
        return list(appworld.load_task_ids(self.dataset_name))

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        return BenchmarkCapabilities(
            state_materialization="official_checkpoint",
            state_snapshot="strict",
            restore_strategy="appworld-checkpoint",
            evaluator_isolation="read_only_or_restored_completion_marker",
            budget_reset="per_attempt_full",
            official_invariance="official_api",
            official_harness="appworld",
            strict_recovery=True,
        )

    def close(self) -> None:
        self._close_world()

    def _uses_official_flow(self) -> bool:
        mode = str(self.options.get("execution_mode") or self.options.get("adapter_mode") or "official").lower()
        return mode not in {"legacy", "legacy-code", "text-code", "python-bridge"}

    def _world_kwargs(self) -> dict[str, Any]:
        kwargs = dict(self.options)
        kwargs.pop("dataset_name", None)
        kwargs.pop("experiment_name", None)
        kwargs.pop("task_ids", None)
        kwargs.pop("root", None)
        kwargs.pop("source_path", None)
        kwargs.pop("clear_completion_marker_on_restore", None)
        return kwargs

    def _configure_root(self) -> None:
        _add_source_path(self.source_path)
        if self.root is None:
            return
        os.environ["APPWORLD_ROOT"] = str(self.root)
        appworld = self._import_appworld()
        if hasattr(appworld, "update_root"):
            appworld.update_root(str(self.root))

    def _import_appworld(self):
        _add_source_path(self.source_path)
        try:
            import appworld
        except Exception as exc:
            raise NotImplementedError(
                "AppWorld benchmark adapter requires the 'appworld' package."
            ) from exc
        _patch_appworld_testclient()
        return appworld

    def _require_world(self):
        if self._world is None:
            raise RuntimeError("AppWorld benchmark has not been reset yet.")
        return self._world

    @staticmethod
    def _state_id_from_snapshot(snapshot: StateSnapshot) -> Any:
        payload = snapshot.payload
        if isinstance(payload, dict) and "state_id" in payload:
            return payload["state_id"]
        return payload

    def _close_world(self) -> None:
        if self._world is not None:
            _close_appworld_world_safely(self._world)
        self._world = None
        self._current_task = None

    @staticmethod
    def _jsonable_task(task: Any) -> Any:
        if hasattr(task, "model_dump"):
            return task.model_dump(mode="json")
        if isinstance(task, dict):
            return task
        try:
            return json.loads(json.dumps(task, default=str))
        except Exception:
            return str(task)


def _add_source_path(source_path: Path | None) -> None:
    if source_path is None:
        return
    candidates = [source_path]
    if (source_path / "src").exists():
        candidates.insert(0, source_path / "src")
    for candidate in candidates:
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)


def _default_source_path() -> Path | None:
    runtime_path = Path("external/appworld/runtime")
    if runtime_path.exists():
        return runtime_path
    source_path = Path("external/appworld/src")
    return source_path if source_path.exists() else None


def _patch_appworld_testclient() -> None:
    global _APPWORLD_TESTCLIENT_PATCHED
    if _APPWORLD_TESTCLIENT_PATCHED:
        return
    try:
        from fastapi.testclient import TestClient
        from appworld.apps import build_main_app
        from appworld.requester import Requester
    except Exception:
        return

    def _get_client(self):
        klass = self.__class__
        if self.apps not in klass.clients:
            client = TestClient(build_main_app(list(self.apps)))
            client.exit_stack = contextlib.ExitStack()
            klass.clients[self.apps] = client
        return klass.clients[self.apps]

    Requester._get_client = _get_client
    _APPWORLD_TESTCLIENT_PATCHED = True


def _close_appworld_world_safely(world: Any) -> None:
    """Close AppWorld without tripping stale freezegun state after load_state().

    AppWorld.load_state() calls AppWorld.close_all() and then rebuilds the API
    requester, but it leaves world.time_freezer pointing at a freezer that is no
    longer registered as active. Calling the official world.close() afterward
    tries to stop that stale freezer and can corrupt freezegun's global stack.
    This mirrors the official close order while skipping stale world-level
    freezers.
    """

    if getattr(world, "remote_environment_url", None):
        world._remote_environment_call("close")
        return

    _clear_appworld_db_cache(world)
    temporary_output_path = getattr(world, "temporary_output_db_home_path_on_disk", None)
    if temporary_output_path:
        shutil.rmtree(temporary_output_path, ignore_errors=True)

    if _world_time_freezer_is_active(world):
        _unset_world_datetime_safely(world)
    else:
        _forget_world_time_freezer(world)

    _close_appworld_apis_safely(world)
    _reset_appworld_gc_threshold()


def _clear_appworld_db_cache(world: Any) -> None:
    try:
        from appworld.apps.lib.apis.local_remote import clear_local_dbs_cache, clear_remote_dbs_cache
    except Exception:
        return

    task_id = getattr(world, "task_id", None)
    remote_apis_url = getattr(world, "remote_apis_url", None)
    if remote_apis_url:
        clear_remote_dbs_cache(remote_apis_url, task_id)
    else:
        clear_local_dbs_cache(task_id)


def _world_time_freezer_is_active(world: Any) -> bool:
    time_freezer_id = getattr(world, "time_freezer_id", None)
    time_freezer = getattr(world, "time_freezer", None)
    if not time_freezer_id or time_freezer is None:
        return False
    active_freezers = getattr(world.__class__, "id_to_time_freezer", {})
    return active_freezers.get(time_freezer_id) is time_freezer


def _unset_world_datetime_safely(world: Any) -> None:
    try:
        world._unset_datetime()
    except Exception as exc:
        if not _is_freezegun_stack_error(exc):
            raise
        _forget_world_time_freezer(world)
    else:
        _forget_world_time_freezer(world)


def _close_appworld_apis_safely(world: Any) -> None:
    apis = getattr(world, "apis", None)
    requester = getattr(world, "requester", None)
    if apis is None or not hasattr(apis, "close"):
        return
    try:
        apis.close()
    except Exception as exc:
        if not _is_freezegun_stack_error(exc):
            raise
        _forget_requester_time_freezer(requester)
    else:
        _forget_requester_time_freezer(requester)


def _forget_world_time_freezer(world: Any) -> None:
    time_freezer_id = getattr(world, "time_freezer_id", None)
    active_freezers = getattr(world.__class__, "id_to_time_freezer", None)
    if time_freezer_id and isinstance(active_freezers, dict):
        active_freezers.pop(time_freezer_id, None)
    time_freezer = getattr(world, "time_freezer", None)
    if time_freezer is not None and hasattr(time_freezer, "_freezer"):
        time_freezer._freezer = None


def _forget_requester_time_freezer(requester: Any) -> None:
    if requester is None:
        return
    time_freezer_or_id = getattr(requester, "time_freezer_or_id", None)
    freezer_registry = getattr(requester.__class__, "time_freezers_or_ids", None)
    if freezer_registry is not None and time_freezer_or_id in freezer_registry:
        freezer_registry.remove(time_freezer_or_id)
    freezer_url_registry = getattr(requester.__class__, "time_freezer_id_to_remote_apis_url", None)
    if isinstance(time_freezer_or_id, str) and isinstance(freezer_url_registry, dict):
        freezer_url_registry.pop(time_freezer_or_id, None)
    if time_freezer_or_id is not None and hasattr(time_freezer_or_id, "_freezer"):
        time_freezer_or_id._freezer = None
    requester.time_freezer_or_id = None


def _reset_appworld_gc_threshold() -> None:
    try:
        from appworld.common.system import GCThreshold
    except Exception:
        return
    GCThreshold.reset()


def _is_freezegun_stack_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        isinstance(exc, AttributeError)
        and "fake_names" in message
        or isinstance(exc, IndexError)
        and "pop from empty list" in message
    )


@dataclass(slots=True)
class AppWorldOfficialAgentEnvironment:
    """Recovery Bench wrapper around AppWorld's official simplified agent loop."""

    world: Any
    source_path: Path | None = None
    adapter_options: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.world, name)

    def run_recovery_bench_agent(
        self,
        *,
        task: Task,
        prompt: str,
        model_client: Any,
        context: AgentContext,
        options: dict[str, Any] | None = None,
        **_: Any,
    ) -> AgentRunResult:
        run_options = {**self.adapter_options, **(options or {})}
        try:
            agent = _build_official_appworld_agent(
                self.source_path,
                model_client=model_client,
                options=run_options,
            )
            prompt_source = "recovery_prompt" if context.protocol == "recovery" and context.attempt_index > 1 else "official_instruction"
            instruction = prompt.strip() if prompt_source == "recovery_prompt" else task.prompt
            trajectory_prefix_attempts = 0
            if prompt_source == "recovery_prompt":
                trajectory_prefix_attempts = len(context.previous_attempts)
                instruction = prefix_with_previous_attempt_trajectory(
                    instruction,
                    context.previous_attempts,
                    max_chars=int(run_options.get("recovery_trajectory_max_chars", 0)),
                    max_observation_chars=int(run_options.get("recovery_trajectory_max_observation_chars", 0)),
                ).strip()
            actions = _run_official_appworld_agent_on_world(
                agent=agent,
                world=self.world,
                instruction=instruction,
                prompt_source=prompt_source,
                max_steps=int(run_options.get("max_steps", getattr(agent, "max_steps", APPWORLD_OFFICIAL_MAX_STEPS))),
            )
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return AgentRunResult(
                metadata={"bridge": "appworld-official", "official_flow": True},
                error=f"AppWorld official flow failed: {type(exc).__name__}: {exc}",
            )

        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "appworld-official",
                "official_flow": True,
                "prompt_source": prompt_source,
                "steps": len(actions),
                "max_steps": int(run_options.get("max_steps", getattr(agent, "max_steps", APPWORLD_OFFICIAL_MAX_STEPS))),
                "task_completed": _safe_bool_call(getattr(self.world, "task_completed", None)),
                "official_agent": agent.__class__.__name__,
                "trajectory_prefix_attempts": trajectory_prefix_attempts,
                "effective_instruction": instruction,
                "effective_instruction_chars": len(instruction),
                "effective_instruction_has_trajectory": "Previous failed attempt trajectory:" in instruction,
            },
        )


def _build_official_appworld_agent(source_path: Path | None, *, model_client: Any, options: dict[str, Any]) -> Any:
    _ensure_appworld_agents_importable(source_path)
    from appworld_agents.code.simplified.agent import Agent

    # Import registers the official agent class with AppWorld's FromDict registry.
    import appworld_agents.code.simplified.react_code_agent  # noqa: F401

    agent_config = _official_appworld_agent_config(source_path, model_client=model_client, options=options)
    _ensure_appworld_model_server_url(agent_config)
    _ensure_appworld_openai_constructor_env(agent_config)
    _ensure_appworld_litellm_compatibility()
    return Agent.from_dict(agent_config)


def _official_appworld_agent_config(source_path: Path | None, *, model_client: Any, options: dict[str, Any]) -> dict[str, Any]:
    experiments_root = _appworld_experiments_root(source_path)
    prompt_file_path = str(experiments_root / "prompts" / "react_code_agent" / "instructions.txt")
    max_steps = int(options.get("max_steps", APPWORLD_OFFICIAL_MAX_STEPS))
    model_config = _official_appworld_model_config(model_client, options)
    return {
        "type": "simplified_react_code_agent",
        "model_config": model_config,
        "appworld_config": {"random_seed": int(options.get("random_seed", 123))},
        "logger_config": {"color": False, "verbose": bool(options.get("verbose", False))},
        "prompt_file_path": prompt_file_path,
        "ignore_multiple_calls": bool(options.get("ignore_multiple_calls", True)),
        "max_prompt_length": options.get("max_prompt_length"),
        "max_output_length": options.get("max_output_length"),
        "max_steps": max_steps,
        "log_lm_calls": bool(options.get("log_lm_calls", False)),
    }


def _official_appworld_model_config(model_client: Any, options: dict[str, Any]) -> dict[str, Any]:
    client_options = dict(getattr(model_client, "options", {}) or {})
    provider = str(getattr(model_client, "provider", "") or "").lower()
    model = str(getattr(model_client, "model", "") or "")
    max_tokens = int(client_options.get("max_tokens") or client_options.get("max_output_tokens") or options.get("max_tokens") or 400)
    client_name = str(client_options.get("appworld_client_name") or ("openai" if provider == "openai" else "litellm"))
    config: dict[str, Any] = {
        "name": model if client_name == "openai" and provider == "openai" else _appworld_litellm_model_name(provider, model),
        "client_name": client_name,
        "temperature": float(client_options.get("temperature") or options.get("temperature") or 0),
        "max_tokens": max_tokens,
        "stop": ["```\n"],
        "retry_after_n_seconds": int(client_options.get("retry_after_n_seconds") or options.get("retry_after_n_seconds") or 10),
        "max_retries": int(client_options.get("request_retries") or options.get("request_retries") or 3),
        "use_cache": bool(options.get("use_cache", False)),
        "cost_per_token": _appworld_cost_per_token(client_options, options),
    }
    if provider == "anthropic":
        api_key = _anthropic_api_key(client_options)
        base_url = _anthropic_base_url(client_options, default=None)
        if api_key:
            config["api_key"] = api_key
        if base_url:
            config["base_url"] = base_url
    elif provider == "openai":
        api_key = _openai_api_key(client_options)
        base_url = _openai_base_url(client_options)
        if api_key:
            config["api_key"] = api_key
        if base_url:
            config["base_url"] = base_url
        reasoning = client_options.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else client_options.get("effort")
        if effort:
            config["reasoning_effort"] = effort
    return config


def _appworld_cost_per_token(client_options: dict[str, Any], options: dict[str, Any]) -> dict[str, float]:
    configured = options.get("cost_per_token") or client_options.get("cost_per_token")
    if isinstance(configured, dict):
        return {
            "input_cache_hit": float(configured.get("input_cache_hit", 0.0)),
            "input_cache_miss": float(configured.get("input_cache_miss", 0.0)),
            "input_cache_write": float(configured.get("input_cache_write", 0.0)),
            "output": float(configured.get("output", 0.0)),
        }
    return {"input_cache_hit": 0.0, "input_cache_miss": 0.0, "input_cache_write": 0.0, "output": 0.0}


def _ensure_appworld_model_server_url(agent_config: dict[str, Any]) -> None:
    model_config = agent_config.get("model_config", {})
    if not isinstance(model_config, dict):
        return
    base_url = model_config.get("base_url")
    if base_url and "MODEL_SERVER_URL" not in os.environ:
        os.environ["MODEL_SERVER_URL"] = str(base_url).removesuffix("/v1")


def _ensure_appworld_openai_constructor_env(agent_config: dict[str, Any]) -> None:
    model_config = agent_config.get("model_config", {})
    if not isinstance(model_config, dict):
        return
    if model_config.get("client_name", "litellm") != "litellm":
        return
    os.environ.setdefault("OPENAI_API_KEY", _openai_api_key({}) or "sk-unused-by-litellm")


def _ensure_appworld_litellm_compatibility() -> None:
    try:
        import litellm
    except Exception:
        return
    litellm.drop_params = True


def _appworld_litellm_model_name(provider: str, model: str) -> str:
    if "/" in model:
        return model
    if provider == "anthropic":
        return f"anthropic/{model}"
    if provider == "openai":
        return f"openai/{model}"
    if provider == "gemini":
        return f"gemini/{model}"
    return model


def _run_official_appworld_agent_on_world(
    *,
    agent: Any,
    world: Any,
    instruction: str,
    prompt_source: str,
    max_steps: int,
) -> list[ActionRecord]:
    from appworld_agents.code.simplified.agent import ExecutionIO, Status
    from appworld_agents.code.common.usage_tracker import Usage

    original_instruction = getattr(world.task, "instruction", None)
    actions: list[ActionRecord] = []
    execution_outputs = []
    try:
        setattr(world.task, "instruction", instruction)
        agent.initialize(world)
        _guard_appworld_language_model_fatal_errors(agent)
        for step_index in range(1, max_steps + 1):
            agent.step_number += 1
            execution_inputs, usage, status = agent.next_execution_inputs_usage_and_status(execution_outputs)
            if status.failed:
                actions.append(
                    ActionRecord(
                        action={"type": "official_appworld_status", "step": step_index},
                        observation=status.message,
                        metadata={"prompt_source": prompt_source, "failed": True},
                    )
                )
                break
            assistant_content = _last_appworld_assistant_content(agent)
            raw_outputs = world.batch_execute([input_.content for input_ in execution_inputs])
            execution_outputs = [
                ExecutionIO(content=raw_output, metadata=execution_input.metadata)
                for execution_input, raw_output in zip(execution_inputs, raw_outputs, strict=True)
            ]
            for execution_input, execution_output in zip(execution_inputs, execution_outputs, strict=True):
                actions.append(
                    ActionRecord(
                        action={
                            "type": "official_appworld_execution",
                            "step": step_index,
                            "code": execution_input.content,
                        },
                        observation=execution_output.content,
                        metadata={
                            "prompt_source": prompt_source,
                            **execution_input.metadata,
                            "assistant_content": assistant_content,
                        },
                    )
                )
            agent.usage_tracker.add(world.task_id, usage)
            agent.log_usage()
            if world.task_completed() or agent.usage_tracker.exceeded(world.task_id):
                break
    finally:
        if original_instruction is not None:
            setattr(world.task, "instruction", original_instruction)
        try:
            agent.logger.complete_task()
        except Exception:
            pass
    return actions


def _guard_appworld_language_model_fatal_errors(agent: Any) -> None:
    language_model = getattr(agent, "language_model", None)
    lm_call = getattr(language_model, "lm_call", None)
    if not callable(lm_call) or getattr(lm_call, "_recovery_bench_fatal_guard", False):
        return

    def guarded_lm_call(*args: Any, **kwargs: Any) -> Any:
        try:
            return lm_call(*args, **kwargs)
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            raise

    setattr(guarded_lm_call, "_recovery_bench_fatal_guard", True)
    language_model.lm_call = guarded_lm_call


def _last_appworld_assistant_content(agent: Any) -> str:
    try:
        for message in reversed(getattr(agent, "messages", []) or []):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return str(message.get("content") or "")
    except Exception:
        return ""
    return ""


def _clear_appworld_completion_marker(world: Any) -> bool:
    """Clear AppWorld's per-attempt submit flag while preserving app state.

    AppWorld's supervisor.complete_task() writes a status into the supervisor
    Task row. That status is a run-control marker for the official agent loop,
    not business progress in the apps under test. Recovery needs to inherit
    dirty app state and any previous answer, but still allow the next attempt
    to continue and submit a corrected answer.
    """

    try:
        from appworld.apps.supervisor.constants import NOT_GIVEN_ANSWER
        from appworld.apps.supervisor.models import Task as SupervisorTask

        active_tasks = SupervisorTask.all()
        if not active_tasks:
            return False
        active_task = active_tasks[0]
        changed = active_task.status is not None
        if changed:
            active_task.status = None
            active_task.save()
        return changed
    except Exception:
        return _clear_appworld_completion_marker_via_shell(world)


def _clear_appworld_completion_marker_via_shell(world: Any) -> bool:
    executor = getattr(world, "execute", None)
    if not callable(executor):
        raise RuntimeError("AppWorld world cannot clear completion marker without an execute method.")
    code = """
from appworld.apps.supervisor.constants import NOT_GIVEN_ANSWER as _rb_not_given_answer
from appworld.apps.supervisor.models import Task as _rb_supervisor_task
_rb_active_tasks = _rb_supervisor_task.all()
_rb_cleared = False
if _rb_active_tasks:
    _rb_active_task = _rb_active_tasks[0]
    _rb_cleared = _rb_active_task.status is not None
    if _rb_cleared:
        _rb_active_task.status = None
        _rb_active_task.save()
print(f"recovery_bench_completion_marker_cleared={_rb_cleared}")
del _rb_not_given_answer, _rb_supervisor_task, _rb_active_tasks, _rb_cleared
if "_rb_active_task" in globals():
    del _rb_active_task
""".strip()
    output = str(executor(code))
    if "Execution failed" in output or "Traceback" in output:
        raise RuntimeError(f"Failed to clear AppWorld completion marker: {output}")
    return "recovery_bench_completion_marker_cleared=True" in output


def _ensure_appworld_agents_importable(source_path: Path | None) -> None:
    _add_source_path(source_path)
    _ensure_appworld_writable_cache()
    try:
        import appworld_agents  # noqa: F401
        return
    except Exception:
        pass

    experiments_root = _appworld_experiments_root(source_path)
    init_path = experiments_root / "__init__.py"
    if not init_path.exists():
        raise RuntimeError(
            "AppWorld official agent package is not importable. Install external/appworld/runtime/experiments "
            "or provide benchmark.options.source_path with an experiments/ directory."
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "appworld_agents",
        init_path,
        submodule_search_locations=[str(experiments_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load AppWorld agents package from {experiments_root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["appworld_agents"] = module
    spec.loader.exec_module(module)


def _ensure_appworld_writable_cache() -> None:
    configured_cache = os.environ.get("APPWORLD_CACHE")
    cache_root = Path(configured_cache) if configured_cache else Path.cwd() / ".cache" / "appworld"
    if not _appworld_cache_is_writable(cache_root):
        cache_root = Path.cwd() / ".cache" / "appworld"
        if not _appworld_cache_is_writable(cache_root):
            raise OSError(f"AppWorld cache path is not writable: {cache_root}")
    os.environ["APPWORLD_CACHE"] = str(cache_root)
    path_store_module = sys.modules.get("appworld.common.path_store")
    path_store = getattr(path_store_module, "path_store", None) if path_store_module is not None else None
    reload = getattr(path_store, "reload", None)
    if callable(reload):
        reload()


def _appworld_cache_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".recovery_bench_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _appworld_experiments_root(source_path: Path | None) -> Path:
    candidates: list[Path] = []
    if source_path is not None:
        candidates.append(source_path / "experiments")
        candidates.append(source_path)
    candidates.extend([Path("external/appworld/runtime/experiments"), Path("external/appworld/src/experiments")])
    for candidate in candidates:
        if (candidate / "code" / "simplified" / "agent.py").exists():
            return candidate
    raise RuntimeError("AppWorld official experiments package was not found.")


@dataclass(slots=True)
class AppWorldAgentEnvironment:
    """Recovery Bench bridge over an AppWorld task world."""

    world: Any
    adapter_options: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.world, name)

    def run_recovery_bench_agent(
        self,
        *,
        task: Task,
        prompt: str,
        model_client: Any,
        context: AgentContext,
        options: dict[str, Any] | None = None,
        **_: Any,
    ) -> AgentRunResult:
        run_options = {**self.adapter_options, **(options or {})}
        max_steps = int(run_options.get("max_steps", APPWORLD_OFFICIAL_MAX_STEPS))
        actions: list[ActionRecord] = []
        observations: list[str] = []
        last_output: str | None = None

        for step_index in range(1, max_steps + 1):
            step_prompt = _build_appworld_step_prompt(
                prompt=prompt,
                task=task,
                step_index=step_index,
                last_output=last_output,
                observations=observations,
            )
            response = model_client.complete(step_prompt, context=context)
            response_text = str(getattr(response, "text", response))
            code = _extract_python_code(response_text)
            if not code.strip():
                return AgentRunResult(
                    actions=tuple(actions),
                    metadata={"bridge": "appworld", "steps": step_index - 1},
                    error="Model did not produce executable AppWorld code.",
                )

            try:
                output = self.world.execute(code)
            except Exception as exc:
                output = f"{type(exc).__name__}: {exc}"

            output_text = str(output)
            last_output = output_text
            observations.append(output_text)
            actions.append(
                ActionRecord(
                    action={"type": "python_code", "step": step_index, "code": code},
                    observation=output_text,
                    metadata={"model_response": response_text},
                )
            )

            task_completed = getattr(self.world, "task_completed", None)
            if callable(task_completed) and bool(task_completed()):
                break

        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "appworld",
                "steps": len(actions),
                "max_steps": max_steps,
                "task_completed": _safe_bool_call(getattr(self.world, "task_completed", None)),
            },
        )


def _build_appworld_step_prompt(
    *,
    prompt: str,
    task: Task,
    step_index: int,
    last_output: str | None,
    observations: list[str],
) -> str:
    history = "\n\n".join(
        f"Observation {index + 1}:\n{observation}" for index, observation in enumerate(observations[-3:])
    )
    parts = [
        prompt.strip(),
        "",
        "You are controlling an AppWorld Python execution shell.",
        "Respond with exactly one Python code block to execute next.",
        "Call the Supervisor complete_task API when the original task is done.",
        "",
        f"Task id: {task.task_id}",
        f"Step: {step_index}",
    ]
    if history:
        parts.extend(["", "Recent execution observations:", history])
    if last_output is not None:
        parts.extend(["", "Last execution output:", last_output])
    return "\n".join(parts).strip() + "\n"


def _extract_python_code(text: str) -> str:
    fenced = re.findall(r"```(?:python|py)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[-1].strip()
    return text.strip()


def _safe_bool_call(func: Any) -> bool | None:
    if not callable(func):
        return None
    try:
        return bool(func())
    except Exception:
        return None
