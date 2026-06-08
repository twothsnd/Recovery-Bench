from __future__ import annotations

import json
import os
import re
import sys
import inspect
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io import dump_dataclass_json
from ..agents.provider import _anthropic_api_key, _anthropic_base_url, _openai_api_key, _openai_base_url
from ..errors import raise_if_fatal_api_error
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


def can_build_tau_bench() -> bool:
    available, _reason = tau_bench_dependency_status()
    return available


def tau_bench_dependency_status() -> tuple[bool, str]:
    _add_source_path(_default_source_path())
    try:
        from tau2.data_model.simulation import TextRunConfig  # noqa: F401
        from tau2.run import run_single_task  # noqa: F401
    except Exception as exc:
        return (
            False,
            "τ-bench import failed. Install tau2-bench or set "
            "benchmark.options.source_path to a downloaded archive checkout. "
            f"Import error: {type(exc).__name__}: {exc}",
        )
    return True, "τ-bench official runtime import succeeded."


def build_tau_bench_benchmark(
    *,
    source_path: Path | None = None,
    domain: str = "airline",
    task_ids: tuple[str, ...] = (),
    solo_mode: bool = False,
    options: dict[str, Any] | None = None,
) -> "TauBenchBenchmarkAdapter":
    _add_source_path(source_path)
    available, reason = tau_bench_dependency_status()
    if not available:
        raise NotImplementedError(reason)
    adapter_options = dict(options or {})
    restore_mode = str(adapter_options.pop("restore_mode", "live"))
    return TauBenchBenchmarkAdapter(
        source_path=source_path,
        domain=domain,
        task_ids=task_ids,
        solo_mode=solo_mode,
        restore_mode=restore_mode,
        options=adapter_options,
    )


@dataclass(slots=True)
class TauBenchBenchmarkAdapter(BenchmarkAdapter):
    """τ-bench adapter using the official runner/orchestrator flow by default."""

    source_path: Path | None = None
    domain: str = "airline"
    task_ids: tuple[str, ...] = ()
    solo_mode: bool = False
    restore_mode: str = "live"
    options: dict[str, Any] = field(default_factory=dict)
    name: str = "tau-bench"
    _env: Any = field(default=None, init=False, repr=False)
    _tau_task: Any = field(default=None, init=False, repr=False)
    _state_simulation_run: Any = field(default=None, init=False, repr=False)
    _last_simulation_run: Any = field(default=None, init=False, repr=False)
    _current_task: Task | None = field(default=None, init=False, repr=False)
    _current_info: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def load_task(self, task_id: str) -> Task:
        if self._uses_official_flow():
            self._import_runtime()
            tau_task = self._load_tau_task(task_id)
            return Task(
                task_id=task_id,
                prompt=_official_tau_task_prompt(self.domain, task_id),
                metadata={
                    "domain": self.domain,
                    "official_flow": True,
                    "task": self._jsonable(tau_task),
                },
            )
        self._import_runtime()
        env = self._make_env(task_id)
        try:
            observation, info = env.reset()
            task_obj = info.get("task")
            prompt = self._build_prompt(task_obj, info)
            metadata = {
                "domain": self.domain,
                "solo_mode": self.solo_mode,
                "task": self._jsonable(task_obj),
                "reset_observation": observation,
            }
            return Task(task_id=task_id, prompt=prompt, metadata=metadata)
        finally:
            self._close_env(env)

    def reset(self, task: Task) -> StateSnapshot:
        if self._uses_official_flow():
            self._close_env()
            self._tau_task = self._load_tau_task(task.task_id)
            self._state_simulation_run = None
            self._last_simulation_run = None
            self._current_task = task
            self._current_info = {}
            return self.snapshot(label="reset")
        self._close_env()
        self._env = self._make_env(task.task_id)
        observation, info = self._env.reset()
        self._current_task = task
        self._current_info = dict(info)
        self._current_info["reset_observation"] = observation
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        if self._uses_official_flow():
            payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
            simulation_run_json = payload.get("simulation_run")
            self._state_simulation_run = (
                _simulation_run_from_json(simulation_run_json) if simulation_run_json else None
            )
            self._last_simulation_run = self._state_simulation_run
            return self.snapshot(label=snapshot.label)
        env = self._require_env()
        payload = snapshot.payload if isinstance(snapshot.payload, dict) else {"simulation_run": snapshot.payload}
        strategy = str(payload.get("restore_strategy", self.restore_mode))
        if strategy == "live":
            if payload.get("live_env_id") != id(env):
                raise RuntimeError(
                    "τ-bench live restore requires the same environment instance. "
                    "Use restore_mode='replay' only after validating it against the installed tau2-bench version."
                )
        elif strategy == "replay":
            simulation_run_json = payload.get("simulation_run", "{}")
            self._replay_state(env, simulation_run_json)
        else:
            raise RuntimeError(f"Unsupported τ-bench restore strategy: {strategy}")
        self._current_info = self._get_info(env)
        return self.snapshot(label=snapshot.label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        if self._uses_official_flow():
            payload = {
                "domain": self.domain,
                "restore_strategy": "tau-official-message-history-replay",
                "task_id": self._current_task.task_id if self._current_task else None,
                "simulation_run": (
                    self._state_simulation_run.model_dump_json()
                    if self._state_simulation_run is not None
                    else None
                ),
                "solo_mode": self.solo_mode,
            }
            return StateSnapshot(
                payload=payload,
                label=label,
                metadata={"benchmark": self.name, "domain": self.domain, "official_flow": True},
            )
        env = self._require_env()
        info = self._get_info(env)
        payload = {
            "domain": self.domain,
            "live_env_id": id(env),
            "restore_strategy": self.restore_mode,
            "task_id": self._current_task.task_id if self._current_task else None,
            "simulation_run": info.get("simulation_run", "{}"),
            "solo_mode": self.solo_mode,
        }
        return StateSnapshot(
            payload=payload,
            label=label,
            metadata={"benchmark": self.name, "domain": self.domain},
        )

    def agent_environment(self) -> Any:
        if self._uses_official_flow():
            return TauBenchOfficialAgentEnvironment(
                official_task=self._require_tau_task(),
                start_simulation_run=self._state_simulation_run,
                domain=self.domain,
                solo_mode=self.solo_mode,
                adapter_options=dict(self.options),
                state_sink=self._record_official_simulation,
            )
        return TauBenchAgentEnvironment(
            env=self._require_env(),
            task=self._current_task,
            reset_observation=self._current_info.get("reset_observation"),
            adapter_options=dict(self.options),
        )

    def evaluate(self, task: Task) -> TaskOutcome:
        if self._uses_official_flow():
            if self._last_simulation_run is None or self._last_simulation_run.reward_info is None:
                return TaskOutcome(success=False, score=0.0, details={"reward_info": None})
            reward_info = self._last_simulation_run.reward_info
            reward = float(getattr(reward_info, "reward", 0.0) or 0.0)
            return TaskOutcome(
                success=reward > 0.0,
                score=reward,
                details=self._jsonable(reward_info),
            )
        env = self._require_env()
        reward, reward_info = self._reward(env)
        success = reward > 0.0
        details = self._jsonable(reward_info)
        return TaskOutcome(success=success, score=reward, details=details)

    def list_tasks(self) -> list[str]:
        if self.task_ids:
            return list(self.task_ids)
        self._import_runtime()

        return [str(task.id) for task in self._load_tau_tasks()]

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        official = self._uses_official_flow()
        return BenchmarkCapabilities(
            state_materialization=(
                "official_message_history_replay" if official else f"{self.restore_mode}_gym_state"
            ),
            state_snapshot="validated_clone" if official else self.restore_mode,
            restore_strategy=(
                "tau-official-message-history-replay" if official else self.restore_mode
            ),
            evaluator_isolation="read_only_reward_info",
            budget_reset="per_attempt_full",
            official_invariance="official_runner" if official else "wrapped_gym",
            official_harness="tau-bench",
            strict_recovery=official or self.restore_mode == "replay",
            limitations=(
                ()
                if official
                else ("Legacy gym restore semantics depend on the installed tau-bench environment.",)
            ),
        )

    def close(self) -> None:
        self._close_env()

    def _import_runtime(self) -> None:
        _add_source_path(self.source_path)
        try:
            from tau2.data_model.simulation import TextRunConfig  # noqa: F401
            from tau2.run import run_single_task  # noqa: F401
        except Exception as exc:
            raise NotImplementedError(
                "τ-bench adapter requires the tau2-bench package installed."
            ) from exc

    def _uses_official_flow(self) -> bool:
        mode = str(self.options.get("execution_mode") or self.options.get("adapter_mode") or "official").lower()
        return mode not in {"legacy", "legacy-gym", "gym", "text-action"}

    def _load_tau_task(self, task_id: str) -> Any:
        self._import_runtime()

        tasks = self._load_tau_tasks(task_ids=[str(task_id)])
        if not tasks:
            raise ValueError(f"τ-bench task {task_id!r} was not found in task set {self._task_set_name()!r}")
        return tasks[0]

    def _task_set_name(self) -> str:
        return str(self.options.get("task_set_name") or self.domain)

    def _load_tau_tasks(self, task_ids: list[str] | None = None) -> list[Any]:
        from tau2.registry import registry

        task_set_name = self._task_set_name()
        task_split_name = self.options.get("task_split_name", "base")
        loader = registry.get_tasks_loader(task_set_name)
        parameters = inspect.signature(loader).parameters
        if "task_split_name" in parameters:
            tasks = loader(task_split_name=task_split_name)
        else:
            tasks = loader()
        if task_ids is not None:
            wanted = set(task_ids)
            tasks = [task for task in tasks if str(task.id) in wanted]
            if len(tasks) != len(wanted):
                missing = wanted - {str(task.id) for task in tasks}
                raise ValueError(f"Not all tasks were found for task set {task_set_name}: {missing}")
        return list(tasks)

    def _require_tau_task(self) -> Any:
        if self._tau_task is None:
            raise RuntimeError("τ-bench benchmark has not been reset yet.")
        return self._tau_task

    def _record_official_simulation(self, simulation_run: Any) -> None:
        self._state_simulation_run = simulation_run
        self._last_simulation_run = simulation_run

    def _make_env(self, task_id: str):
        self._import_runtime()
        import gymnasium as gym

        TAU_BENCH_ENV_ID, _ = _import_gym_symbols()

        _register_tau_gym_agent_once()
        env_kwargs = dict(self.options)
        env_kwargs.pop("domain", None)
        env_kwargs.pop("task_ids", None)
        env_kwargs.pop("solo_mode", None)
        env_kwargs.pop("source_path", None)
        env_kwargs.pop("restore_mode", None)
        env_kwargs = _prepare_tau_env_kwargs(env_kwargs)
        return gym.make(
            TAU_BENCH_ENV_ID,
            domain=self.domain,
            task_id=task_id,
            solo_mode=self.solo_mode,
            **env_kwargs,
        )

    def _require_env(self):
        if self._env is None:
            raise RuntimeError("τ-bench benchmark has not been reset yet.")
        return self._env

    def _get_info(self, env) -> dict[str, Any]:
        env = _unwrap_env(env)
        getter = getattr(env, "_get_info", None)
        if callable(getter):
            return dict(getter())
        if hasattr(env, "get_info") and callable(env.get_info):
            return dict(env.get_info())
        return {}

    def _reward(self, env) -> tuple[float, Any]:
        env = _unwrap_env(env)
        getter = getattr(env, "_get_reward", None)
        if callable(getter):
            return getter()
        if hasattr(env, "get_reward") and callable(env.get_reward):
            return env.get_reward()
        return 0.0, {}

    def _replay_state(self, env, simulation_run_json: str) -> None:
        from copy import deepcopy

        from tau2.data_model.simulation import SimulationRun
        from tau2.gym.gym_agent import GymAgentState, GymUserState

        env = _unwrap_env(env)
        simulation_run = SimulationRun.model_validate_json(simulation_run_json)
        task = getattr(getattr(env, "_orchestrator", None), "task", None)
        if task is None:
            raise RuntimeError("τ-bench environment orchestrator is not available for restore.")
        initial_state = getattr(task, "initial_state", None)
        environment = getattr(getattr(env, "_orchestrator", None), "environment", None)
        if environment is None:
            raise RuntimeError("τ-bench environment instance is not available for restore.")
        initialization_data = getattr(initial_state, "initialization_data", None) if initial_state else None
        initialization_actions = getattr(initial_state, "initialization_actions", None) if initial_state else None
        message_history = simulation_run.get_messages()
        environment.set_state(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=message_history,
        )
        orchestrator = getattr(env, "_orchestrator", None)
        if orchestrator is not None:
            if hasattr(orchestrator, "agent_state"):
                orchestrator.agent_state = GymAgentState(messages=deepcopy(message_history))
            if hasattr(orchestrator, "user_state"):
                orchestrator.user_state = GymUserState(messages=deepcopy(message_history))
        agent = getattr(env, "_agent", None)
        if agent is not None and hasattr(agent, "_observation"):
            agent._observation = deepcopy(message_history)
        user = getattr(env, "_user", None)
        if user is not None and hasattr(user, "_observation"):
            user._observation = deepcopy(message_history)
        if hasattr(env, "_simulation_run"):
            env._simulation_run = simulation_run
        if hasattr(env, "_simulation_done"):
            env._simulation_done.clear()

    @staticmethod
    def _build_prompt(task_obj: Any, info: dict[str, Any]) -> str:
        policy = info.get("policy", "")
        task_text = ""
        if task_obj is not None:
            ticket = getattr(task_obj, "ticket", None)
            if ticket:
                task_text = str(ticket)
            else:
                scenario = getattr(task_obj, "user_scenario", None)
                instructions = getattr(scenario, "instructions", None) if scenario is not None else None
                task_text = str(instructions or scenario or task_obj)
        parts = [
            "Complete the original task.",
            "",
            "Policy:",
            str(policy).strip(),
            "",
            "Original task:",
            task_text.strip(),
        ]
        return "\n".join(parts).strip() + "\n"

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): TauBenchBenchmarkAdapter._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [TauBenchBenchmarkAdapter._jsonable(item) for item in value]
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return str(value)

    def _close_env(self, env: Any | None = None) -> None:
        target = env or self._env
        if target is not None and hasattr(target, "close"):
            target.close()
        if env is None:
            self._env = None
            self._tau_task = None
            self._state_simulation_run = None
            self._last_simulation_run = None
            self._current_task = None
            self._current_info = {}


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


def _import_gym_symbols():
    try:
        from tau2.gym import TAU_BENCH_ENV_ID, register_gym_agent
    except Exception:
        from tau2.gym.gym_agent import TAU_BENCH_ENV_ID, register_gym_agent
    return TAU_BENCH_ENV_ID, register_gym_agent


def _register_tau_gym_agent_once() -> None:
    import gymnasium as gym

    TAU_BENCH_ENV_ID, register_gym_agent = _import_gym_symbols()
    registry = getattr(getattr(gym, "envs", None), "registry", {})
    if TAU_BENCH_ENV_ID in registry:
        return
    register_gym_agent()


def _unwrap_env(env: Any) -> Any:
    return getattr(env, "unwrapped", env)


def _default_source_path() -> Path | None:
    source_path = Path("external/tau-bench/src")
    return source_path if source_path.exists() else None


def _prepare_tau_env_kwargs(env_kwargs: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(env_kwargs)
    user_llm_args = dict(prepared.get("user_llm_args") or {})
    user_llm = str(prepared.get("user_llm") or "")
    user_llm_normalized = user_llm.lower()
    uses_openai_compat = user_llm_normalized.startswith("openai/")
    uses_anthropic = user_llm_normalized.startswith("anthropic/") or (
        not uses_openai_compat and "claude" in user_llm_normalized
    )
    if uses_anthropic:
        if "api_key" not in user_llm_args:
            api_key = _anthropic_api_key({})
            if api_key:
                user_llm_args["api_key"] = api_key
        if "api_base" not in user_llm_args and "base_url" not in user_llm_args:
            base_url = _anthropic_base_url({}, default=None)
            if base_url:
                user_llm_args["api_base"] = base_url
                user_llm_args["base_url"] = base_url
    else:
        if "api_key" not in user_llm_args:
            api_key = _openai_api_key({})
            if api_key:
                user_llm_args["api_key"] = api_key
        if "api_base" not in user_llm_args and "base_url" not in user_llm_args:
            base_url = _openai_base_url({})
            if base_url:
                user_llm_args["api_base"] = base_url
                user_llm_args["base_url"] = base_url
    if user_llm_args:
        prepared["user_llm_args"] = user_llm_args
    if "OPENAI_API_KEY" not in os.environ:
        api_key = _openai_api_key({})
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
    if "OPENAI_BASE_URL" not in os.environ:
        base_url = _openai_base_url({})
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
    if "ANTHROPIC_API_KEY" not in os.environ:
        api_key = _anthropic_api_key({})
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
    return prepared


@dataclass(slots=True)
class TauBenchOfficialAgentEnvironment:
    """Recovery Bench wrapper around τ-bench's official text runner."""

    official_task: Any
    start_simulation_run: Any = None
    domain: str = "airline"
    solo_mode: bool = False
    adapter_options: dict[str, Any] = field(default_factory=dict)
    state_sink: Any = None

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
        prompt_source = (
            "recovery_prompt"
            if context.protocol == "recovery" and context.attempt_index > 1
            else "official_conversation"
        )
        try:
            config = _official_tau_text_config(
                domain=self.domain,
                solo_mode=self.solo_mode,
                model_client=model_client,
                options=run_options,
            )
            official_task = _official_tau_task_for_attempt(
                self.official_task,
                start_simulation_run=self.start_simulation_run,
                recovery_prompt=prompt if prompt_source == "recovery_prompt" else None,
            )
            simulation_run = _run_official_tau_task(
                config=config,
                official_task=official_task,
                options=run_options,
            )
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return AgentRunResult(
                metadata={"bridge": "tau-bench-official", "official_flow": True},
                error=f"τ-bench official flow failed: {type(exc).__name__}: {exc}",
            )

        if callable(self.state_sink):
            self.state_sink(simulation_run)

        messages = simulation_run.get_messages()
        return AgentRunResult(
            actions=tuple(_tau_action_records_from_messages(messages, prompt_source=prompt_source)),
            metadata={
                "bridge": "tau-bench-official",
                "official_flow": True,
                "prompt_source": prompt_source,
                "recovery_injection": "initial_state_user_message" if prompt_source == "recovery_prompt" else None,
                "steps": len(messages),
                "max_steps": int(run_options.get("max_steps", config.max_steps)),
                "termination_reason": str(simulation_run.termination_reason),
                "reward": (
                    float(simulation_run.reward_info.reward)
                    if simulation_run.reward_info is not None
                    else None
                ),
                "official_agent": config.effective_agent,
                "official_user": config.effective_user,
                "llm_agent": config.llm_agent,
                "llm_user": config.llm_user,
            },
        )


def _official_tau_text_config(*, domain: str, solo_mode: bool, model_client: Any, options: dict[str, Any]) -> Any:
    from tau2.config import DEFAULT_MAX_STEPS
    from tau2.data_model.simulation import TextRunConfig

    agent_name = str(options.get("agent") or ("llm_agent_solo" if solo_mode else "llm_agent"))
    user_name = str(options.get("user") or ("dummy_user" if solo_mode else "user_simulator"))
    llm_agent = str(options.get("llm_agent") or _tau_litellm_model_name(model_client))
    llm_args_agent = {
        **dict(options.get("llm_args_agent") or options.get("agent_llm_args") or {}),
        **_tau_litellm_args_for_model_client(model_client, options),
    }
    llm_user = str(options.get("llm_user") or options.get("user_llm") or "gpt-4.1")
    llm_args_user = dict(options.get("llm_args_user") or options.get("user_llm_args") or {})
    prepared_user = _prepare_tau_env_kwargs({"user_llm": llm_user, "user_llm_args": llm_args_user})
    llm_user = str(prepared_user.get("user_llm", llm_user))
    llm_args_user = dict(prepared_user.get("user_llm_args", llm_args_user))

    return TextRunConfig(
        domain=domain,
        task_set_name=options.get("task_set_name"),
        task_split_name=options.get("task_split_name", "base"),
        agent=agent_name,
        user=user_name,
        llm_agent=llm_agent,
        llm_args_agent=llm_args_agent,
        llm_user=llm_user,
        llm_args_user=llm_args_user,
        max_steps=int(options.get("max_steps", DEFAULT_MAX_STEPS)),
        max_errors=int(options.get("max_errors", 10)),
        seed=options.get("seed"),
        timeout=options.get("timeout"),
        enforce_communication_protocol=bool(options.get("enforce_communication_protocol", False)),
        verbose_logs=bool(options.get("verbose_logs", False)),
        auto_review=bool(options.get("auto_review", False)),
        review_mode=str(options.get("review_mode", "full")),
        retrieval_config=options.get("retrieval_config"),
        retrieval_config_kwargs=options.get("retrieval_config_kwargs"),
    )


def _tau_litellm_model_name(model_client: Any) -> str:
    provider = str(getattr(model_client, "provider", "") or "").lower()
    model = str(getattr(model_client, "model", "") or "")
    if "/" in model:
        return model
    if provider == "anthropic":
        return f"anthropic/{model}"
    if provider == "openai":
        return f"openai/{model}"
    if provider == "gemini":
        return f"gemini/{model}"
    return model


def _tau_litellm_args_for_model_client(model_client: Any, options: dict[str, Any]) -> dict[str, Any]:
    client_options = dict(getattr(model_client, "options", {}) or {})
    provider = str(getattr(model_client, "provider", "") or "").lower()
    args: dict[str, Any] = {}
    max_tokens = client_options.get("max_tokens") or client_options.get("max_output_tokens") or options.get("max_tokens")
    if max_tokens is not None:
        args["max_tokens"] = int(max_tokens)
    temperature = client_options.get("temperature", options.get("temperature"))
    if temperature is not None:
        args["temperature"] = float(temperature)
    request_retries = client_options.get("request_retries") or options.get("request_retries")
    if request_retries is not None:
        args["num_retries"] = int(request_retries)
    if provider == "anthropic":
        api_key = _anthropic_api_key(client_options)
        base_url = _anthropic_base_url(client_options, default=None)
        if api_key:
            args["api_key"] = api_key
        if base_url:
            args["api_base"] = base_url
            args["base_url"] = base_url
    elif provider == "openai":
        api_key = _openai_api_key(client_options)
        base_url = _openai_base_url(client_options)
        if api_key:
            args["api_key"] = api_key
        if base_url:
            args["api_base"] = base_url
            args["base_url"] = base_url
        custom_provider = client_options.get("custom_llm_provider")
        if custom_provider:
            args["custom_llm_provider"] = custom_provider
        reasoning = client_options.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else client_options.get("effort")
        if effort:
            args["reasoning_effort"] = effort
    return args


def _official_tau_task_for_attempt(
    official_task: Any,
    *,
    start_simulation_run: Any,
    recovery_prompt: str | None,
) -> Any:
    from tau2.data_model.message import UserMessage
    from tau2.data_model.tasks import InitialState

    task_copy = official_task.model_copy(deep=True)
    original_initial_state = task_copy.initial_state
    initial_state = (
        original_initial_state.model_copy(deep=True)
        if original_initial_state is not None
        else InitialState()
    )
    if start_simulation_run is not None:
        message_history = deepcopy(start_simulation_run.get_messages())
    else:
        message_history = deepcopy(initial_state.message_history or [])
    if recovery_prompt:
        message_history.append(
            UserMessage(
                role="user",
                content=recovery_prompt,
                cost=0.0,
                usage={"prompt_tokens": 0, "completion_tokens": 0},
            )
        )
    initial_state.message_history = message_history or None
    task_copy.initial_state = initial_state
    return task_copy


def _run_official_tau_task(*, config: Any, official_task: Any, options: dict[str, Any]) -> Any:
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.run import run_single_task

    evaluation_type = EvaluationType(str(options.get("evaluation_type", EvaluationType.ALL.value)))
    return run_single_task(
        config,
        official_task,
        seed=config.seed,
        evaluation_type=evaluation_type,
        verbose_logs=bool(options.get("verbose_logs", False)),
        auto_review=bool(options.get("auto_review", False)),
        review_mode=str(options.get("review_mode", "full")),
    )


def _tau_action_records_from_messages(messages: list[Any], *, prompt_source: str) -> list[ActionRecord]:
    actions: list[ActionRecord] = []
    for index, message in enumerate(messages, start=1):
        tool_calls = getattr(message, "tool_calls", None)
        actions.append(
            ActionRecord(
                action={
                    "type": "tau_bench_message",
                    "step": index,
                    "role": getattr(message, "role", None),
                    "tool_calls": TauBenchBenchmarkAdapter._jsonable(tool_calls) if tool_calls else None,
                },
                observation=str(message),
                metadata={"prompt_source": prompt_source},
            )
        )
    return actions


def _simulation_run_from_json(simulation_run_json: str) -> Any:
    from tau2.data_model.simulation import SimulationRun

    return SimulationRun.model_validate_json(simulation_run_json)


def _official_tau_task_prompt(domain: str, task_id: str) -> str:
    return (
        f"Complete the official τ-bench {domain} task {task_id} through the benchmark's "
        "standard user simulator and environment."
    )


@dataclass(slots=True)
class TauBenchAgentEnvironment:
    """Recovery Bench bridge over the τ-bench Gym environment."""

    env: Any
    task: Task | None
    reset_observation: Any = None
    adapter_options: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.env, name)

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
        max_steps = int(run_options.get("max_steps", 200))
        actions: list[ActionRecord] = []
        observation = self.reset_observation
        reward: float | None = None
        terminated = False
        truncated = False
        info: Any = {}

        for step_index in range(1, max_steps + 1):
            step_prompt = _build_tau_step_prompt(
                prompt=prompt,
                task=task,
                step_index=step_index,
                observation=observation,
            )
            response = model_client.complete(step_prompt, context=context)
            response_text = str(getattr(response, "text", response))
            action = _extract_tau_action(response_text)
            if not action:
                return AgentRunResult(
                    actions=tuple(actions),
                    metadata={"bridge": "tau-bench-gym", "steps": step_index - 1},
                    error="Model did not produce a τ-bench action string.",
                )

            try:
                observation, reward, terminated, truncated, info = self.env.step(action)
            except Exception as exc:
                return AgentRunResult(
                    actions=tuple(actions),
                    metadata={"bridge": "tau-bench-gym", "steps": step_index - 1},
                    error=f"τ-bench env.step failed: {type(exc).__name__}: {exc}",
                )

            actions.append(
                ActionRecord(
                    action={"type": "tau_bench_action", "step": step_index, "text": action},
                    observation=observation,
                    metadata={
                        "model_response": response_text,
                        "reward": reward,
                        "terminated": terminated,
                        "truncated": truncated,
                        "info": TauBenchBenchmarkAdapter._jsonable(info),
                    },
                )
            )
            if terminated or truncated:
                break

        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "tau-bench-gym",
                "steps": len(actions),
                "max_steps": max_steps,
                "last_reward": reward,
                "terminated": terminated,
                "truncated": truncated,
                "last_info": TauBenchBenchmarkAdapter._jsonable(info),
            },
        )


def _build_tau_step_prompt(
    *,
    prompt: str,
    task: Task,
    step_index: int,
    observation: Any,
) -> str:
    parts = [
        prompt.strip(),
        "",
        "You are controlling a τ-bench Gym agent environment.",
        "Respond with exactly one next action string for env.step(action).",
        "Use plain assistant text or a τ-bench tool-call action string as appropriate.",
        "",
        f"Task id: {task.task_id}",
        f"Step: {step_index}",
        "",
        "Current observation:",
        "" if observation is None else str(observation),
    ]
    return "\n".join(parts).strip() + "\n"


def _extract_tau_action(text: str) -> str:
    fenced = re.findall(r"```(?:text|txt)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced[-1] if fenced else text
    candidate = candidate.strip()
    if candidate.lower().startswith("action:"):
        candidate = candidate.split(":", 1)[1].strip()
    if candidate.lower().startswith("assistant:"):
        candidate = candidate.split(":", 1)[1].strip()
    return candidate
