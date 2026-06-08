from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..io import dump_dataclass_json
from ..prompts import prefix_with_previous_attempt_trajectory
from ..types import (
    ActionRecord,
    AgentContext,
    AgentRunResult,
    BenchmarkCapabilities,
    BenchmarkResult,
    StateSnapshot,
    Task,
    TaskOutcome,
)


DEFAULT_SOURCE_PATH = Path("external/osworld/src")
DEFAULT_TEST_ALL_META_PATH = Path("evaluation_examples/test_all.json")


def osworld_dependency_status() -> tuple[bool, str]:
    try:
        source_path = _default_source_path()
        if source_path is not None:
            _add_source_path(source_path)
        _desktop_env_class()
    except Exception as exc:
        return False, f"OSWorld import failed: {type(exc).__name__}: {exc}"
    return True, "OSWorld DesktopEnv import succeeded"


def build_osworld_benchmark(
    *,
    source_path: Path | None = None,
    test_all_meta_path: Path | None = None,
    domain: str = "all",
    task_ids: tuple[str, ...] = (),
    options: dict[str, Any] | None = None,
) -> "OSWorldBenchmarkAdapter":
    source_path = source_path or _default_source_path()
    return OSWorldBenchmarkAdapter(
        source_path=source_path,
        test_all_meta_path=test_all_meta_path or DEFAULT_TEST_ALL_META_PATH,
        domain=domain,
        selected_task_ids=task_ids,
        options=dict(options or {}),
    )


@dataclass(slots=True)
class OSWorldBenchmarkAdapter:
    """Recovery-Bench adapter for OSWorld DesktopEnv.

    Strict recovery uses the provider snapshot API to checkpoint the VM after
    an agent attempt and before official evaluate/postconfig mutates the live
    environment. Docker does not support this; live recovery is available only
    as an explicit, non-strict fallback.
    """

    source_path: Path | None = None
    test_all_meta_path: Path = DEFAULT_TEST_ALL_META_PATH
    domain: str = "all"
    selected_task_ids: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    name: str = "osworld"
    _env: Any = field(default=None, init=False, repr=False)
    _current_task: Task | None = field(default=None, init=False, repr=False)
    _current_example: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _task_index: dict[str, tuple[str, Path]] | None = field(default=None, init=False, repr=False)
    _last_observation: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _snapshot_index: int = field(default=0, init=False, repr=False)

    @property
    def snapshot_after_evaluate(self) -> bool:
        return self._restore_strategy() == "live"

    def load_task(self, task_id: str) -> Task:
        domain, path = self._index()[task_id]
        example = json.loads(path.read_text(encoding="utf-8"))
        return Task(
            task_id=task_id,
            prompt=str(example["instruction"]),
            metadata={
                "benchmark": self.name,
                "domain": domain,
                "example_id": str(example["id"]),
                "example_path": str(path),
                "example": example,
            },
        )

    def reset(self, task: Task) -> StateSnapshot:
        self._current_task = task
        self._current_example = dict(task.metadata["example"])
        self._snapshot_index = 0
        env = self._ensure_env()
        self._validate_checkpoint_support(env)
        self._last_observation = env.reset(task_config=self._current_example)
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
        strategy = payload.get("restore_strategy")
        if strategy == "provider-checkpoint":
            if payload.get("env_id") != id(self._env):
                raise RuntimeError("Cannot restore OSWorld state from a different live environment handle.")
            self._restore_provider_checkpoint(payload)
        elif strategy == "live-osworld-env":
            if self._restore_strategy() != "live":
                raise ValueError("Live OSWorld restore requires benchmark.options.osworld_restore_strategy = 'live'.")
            if payload.get("env_id") != id(self._env):
                raise RuntimeError("Cannot restore OSWorld state from a different live environment handle.")
        else:
            raise ValueError("OSWorld restore requires a provider checkpoint or explicit live snapshot handle.")
        restored_label = f"{snapshot.label}-restored" if snapshot.label else "restore"
        return self.snapshot(label=restored_label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        if self._current_task is None:
            raise RuntimeError("OSWorld snapshot requested before reset().")
        self._snapshot_index += 1
        evaluator = (self._current_example or {}).get("evaluator", {})
        postconfig = evaluator.get("postconfig", []) if isinstance(evaluator, dict) else []
        restore_strategy = self._restore_strategy()
        checkpoint_name = None
        if restore_strategy == "checkpoint" and self._should_checkpoint(label):
            checkpoint_name = self._save_provider_checkpoint(label)
        payload = {
            "benchmark": self.name,
            "task_id": self._current_task.task_id,
            "example_id": self._current_task.metadata.get("example_id"),
            "domain": self._current_task.metadata.get("domain"),
            "env_id": id(self._env),
            "snapshot_index": self._snapshot_index,
            "restore_strategy": "provider-checkpoint" if checkpoint_name else "live-osworld-env",
            "state_materialization": "provider-vm-checkpoint" if checkpoint_name else "opaque-live-vm",
            "evaluation_state_consistency": (
                "recovery restores provider checkpoint captured before official evaluate/postconfig"
                if checkpoint_name
                else "recovery inherits live VM state after official evaluate/postconfig"
            ),
            "checkpoint_name": checkpoint_name,
            "strict_recovery": bool(checkpoint_name),
            "postconfig_count": len(postconfig),
            "action_history_length": len(getattr(self._env, "action_history", []) or []),
            "action_history": list(getattr(self._env, "action_history", []) or []),
            "step_no": getattr(self._env, "_step_no", None),
        }
        return StateSnapshot(
            payload=payload,
            label=label,
            metadata={
                "benchmark": self.name,
                "live_state": not bool(checkpoint_name),
                "restore_strategy": payload["restore_strategy"],
                "evaluation_mutates_live_state": bool(postconfig),
                "strict_recovery": bool(checkpoint_name),
            },
        )

    def agent_environment(self) -> "OSWorldAgentEnvironment":
        if self._env is None or self._current_task is None:
            raise RuntimeError("OSWorld agent environment requested before reset().")
        return OSWorldAgentEnvironment(
            env=self._env,
            task=self._current_task,
            example=self._current_example or {},
            observation_getter=self._get_observation,
            observation_setter=self._set_observation,
            options=dict(self.options),
        )

    def evaluate(self, task: Task) -> TaskOutcome:
        if self._env is None:
            raise RuntimeError("OSWorld evaluate requested before reset().")
        score = float(self._env.evaluate())
        evaluator = (self._current_example or {}).get("evaluator", {})
        postconfig = evaluator.get("postconfig", []) if isinstance(evaluator, dict) else []
        return TaskOutcome(
            success=score > 0.0,
            score=score,
            details={
                "benchmark": self.name,
                "score": score,
                "postconfig_count": len(postconfig),
                "evaluation_mutates_live_state": bool(postconfig),
                "state_consistency": (
                    "official-evaluate-runs-live; strict recovery restores pre-evaluate provider checkpoint"
                    if self._restore_strategy() == "checkpoint"
                    else "live-after-official-evaluate"
                ),
            },
        )

    def list_tasks(self) -> list[str]:
        if self.selected_task_ids:
            return list(self.selected_task_ids)
        return sorted(
            task_id
            for task_id in self._index()
            if "/" not in task_id
        )

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        safe_task_id = result.task_id.replace("/", "__")
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{safe_task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        checkpoint = self._restore_strategy() == "checkpoint"
        return BenchmarkCapabilities(
            state_materialization="provider_vm_checkpoint" if checkpoint else "opaque_live_vm",
            state_snapshot="strict" if checkpoint else "live_handle",
            restore_strategy="provider-checkpoint" if checkpoint else "live-osworld-env",
            evaluator_isolation=(
                "pre_evaluate_provider_checkpoint" if checkpoint else "live_after_official_evaluate"
            ),
            budget_reset="per_attempt_full",
            official_invariance="official_desktop_env",
            official_harness="osworld",
            strict_recovery=checkpoint,
            limitations=(
                ()
                if checkpoint
                else ("Live OSWorld recovery is exploratory because evaluator/postconfig may mutate VM state.",)
            ),
        )

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
            self._env = None

    def _ensure_env(self) -> Any:
        if self._env is not None:
            return self._env
        source_path = self.source_path
        if source_path is not None:
            _add_source_path(source_path)
        DesktopEnv = _desktop_env_class()
        env_options = _desktop_env_options(self.options)
        self._env = DesktopEnv(**env_options)
        return self._env

    def _restore_strategy(self) -> str:
        value = (
            self.options.get("osworld_restore_strategy")
            or self.options.get("recovery_restore_strategy")
            or self.options.get("restore_strategy")
            or "checkpoint"
        )
        strategy = str(value).strip().lower()
        if strategy not in {"checkpoint", "live"}:
            raise ValueError("OSWorld restore strategy must be 'checkpoint' or 'live'.")
        return strategy

    def _validate_checkpoint_support(self, env: Any) -> None:
        if self._restore_strategy() != "checkpoint":
            return
        provider_name = str(self.options.get("provider_name") or getattr(env, "provider_name", "")).lower()
        if provider_name == "docker":
            raise RuntimeError(
                "Strict OSWorld recovery requires provider checkpoint support. "
                "Docker provider does not support snapshots; use vmware/virtualbox/fastvm/cloud, "
                "or set benchmark.options.osworld_restore_strategy = 'live' for exploratory runs."
            )
        if not callable(getattr(env, "_save_state", None)) or not callable(getattr(env, "_revert_to_snapshot", None)):
            raise RuntimeError("Strict OSWorld recovery requires DesktopEnv _save_state and _revert_to_snapshot.")

    def _should_checkpoint(self, label: str | None) -> bool:
        return bool(label and re.fullmatch(r"attempt-\d+-after", label))

    def _save_provider_checkpoint(self, label: str | None) -> str:
        env = self._ensure_env()
        self._validate_checkpoint_support(env)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label or "snapshot").strip("-") or "snapshot"
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", self._current_task.task_id).strip("-") or "task"
        checkpoint_name = f"recovery-bench-{safe_task_id}-{safe_label}-{uuid.uuid4().hex}"
        try:
            env._save_state(checkpoint_name)
        except Exception as exc:
            raise RuntimeError(
                f"OSWorld provider checkpoint failed for {checkpoint_name!r}: {type(exc).__name__}: {exc}"
            ) from exc
        return checkpoint_name

    def _restore_provider_checkpoint(self, payload: dict[str, Any]) -> None:
        checkpoint_name = payload.get("checkpoint_name")
        if not checkpoint_name:
            raise ValueError("OSWorld provider checkpoint snapshot is missing checkpoint_name.")
        env = self._ensure_env()
        self._validate_checkpoint_support(env)
        previous_snapshot_name = getattr(env, "snapshot_name", None)
        try:
            env.snapshot_name = checkpoint_name
            env._revert_to_snapshot()
            starter = getattr(env, "_start_emulator", None)
            if callable(starter):
                starter()
            env.is_environment_used = True
            env.action_history = list(payload.get("action_history") or [])
            if payload.get("step_no") is not None:
                env._step_no = int(payload["step_no"])
            self._last_observation = env._get_obs() if callable(getattr(env, "_get_obs", None)) else None
        except Exception as exc:
            raise RuntimeError(
                f"OSWorld provider checkpoint restore failed for {checkpoint_name!r}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if previous_snapshot_name is not None:
                env.snapshot_name = previous_snapshot_name

    def _index(self) -> dict[str, tuple[str, Path]]:
        if self._task_index is not None:
            return self._task_index
        source_path = self.source_path or Path(".")
        meta_path = self.test_all_meta_path
        if not meta_path.is_absolute():
            meta_path = source_path / meta_path
        test_all_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        examples_dir = meta_path.parent / "examples"
        domains = [self.domain] if self.domain != "all" else sorted(test_all_meta)
        index: dict[str, tuple[str, Path]] = {}
        for domain in domains:
            for example_id in test_all_meta[domain]:
                path = examples_dir / domain / f"{example_id}.json"
                index[str(example_id)] = (domain, path)
                index[f"{domain}/{example_id}"] = (domain, path)
        self._task_index = index
        return index

    def _get_observation(self) -> dict[str, Any]:
        if self._last_observation is not None:
            return self._last_observation
        if self._env is None:
            raise RuntimeError("OSWorld observation requested before reset().")
        self._last_observation = self._env._get_obs()
        return self._last_observation

    def _set_observation(self, observation: dict[str, Any]) -> None:
        self._last_observation = observation


@dataclass(slots=True)
class OSWorldAgentEnvironment:
    env: Any
    task: Task
    example: dict[str, Any]
    observation_getter: Callable[[], dict[str, Any]]
    observation_setter: Callable[[dict[str, Any]], None]
    options: dict[str, Any] = field(default_factory=dict)

    def run_recovery_bench_agent(
        self,
        *,
        prompt: str,
        model_client: Any,
        context: AgentContext,
        options: dict[str, Any],
    ) -> AgentRunResult:
        run_options = {**self.options, **dict(options)}
        max_steps = int(run_options.get("max_steps", 15))
        sleep_after_execution = float(run_options.get("sleep_after_execution", 0.0))
        max_actions_per_step = int(run_options.get("max_actions_per_step", 1))
        trajectory_chars = int(run_options.get("trajectory_prefix_chars", 12000))
        observation_chars = int(run_options.get("trajectory_observation_chars", 1200))
        send_screenshot = bool(run_options.get("send_screenshot", True))

        actions: list[ActionRecord] = []
        done = False
        last_info: dict[str, Any] = {}
        observation = self.observation_getter()
        task_prompt = _osworld_prompt(
            prompt,
            context=context,
            observation=observation,
            previous_chars=trajectory_chars,
            previous_observation_chars=observation_chars,
        )
        if context.previous_attempts:
            task_prompt = prefix_with_previous_attempt_trajectory(
                task_prompt,
                context.previous_attempts,
                max_chars=trajectory_chars,
                max_observation_chars=observation_chars,
            )

        for step_index in range(1, max_steps + 1):
            response = _complete_for_observation(
                model_client=model_client,
                prompt=task_prompt,
                observation=observation,
                context=context,
                send_screenshot=send_screenshot,
            )
            parsed_actions = _extract_osworld_actions(response.text)
            if not parsed_actions:
                return AgentRunResult(
                    actions=tuple(actions),
                    metadata={
                        "bridge": "osworld",
                        "steps": step_index - 1,
                        "unparseable_step": step_index,
                        "unparseable_response": response.text[:8000],
                        "unparseable_response_chars": len(response.text),
                    },
                    error="Model response did not contain an executable OSWorld action.",
                )
            for action in parsed_actions[:max_actions_per_step]:
                try:
                    observation, _reward, done, info = self.env.step(action, pause=sleep_after_execution)
                except Exception as exc:
                    actions.append(
                        ActionRecord(
                            action=action,
                            metadata={
                                "assistant_content": response.text,
                                "step": step_index,
                                "execution_error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    )
                    return AgentRunResult(
                        actions=tuple(actions),
                        metadata={"bridge": "osworld", "steps": step_index},
                        error=f"OSWorld action execution failed: {type(exc).__name__}: {exc}",
                    )
                self.observation_setter(observation)
                last_info = dict(info or {})
                actions.append(
                    ActionRecord(
                        action=action,
                        observation=_compact_observation(observation),
                        metadata={
                            "assistant_content": response.text,
                            "step": step_index,
                            "done": bool(done),
                            "info": last_info,
                        },
                    )
                )
                if done:
                    break
            if done:
                break
            task_prompt = _osworld_prompt(
                prompt,
                context=context,
                observation=observation,
                previous_chars=trajectory_chars,
                previous_observation_chars=observation_chars,
            )

        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "osworld",
                "steps": len(actions),
                "terminated": done,
                "last_info": last_info,
                "state_consistency": "live-after-agent-actions",
            },
        )


def _desktop_env_options(options: dict[str, Any]) -> dict[str, Any]:
    observation_type = str(options.get("observation_type", "a11y_tree"))
    env_options: dict[str, Any] = {
        "provider_name": str(options.get("provider_name", "vmware")),
        "path_to_vm": options.get("path_to_vm"),
        "action_space": str(options.get("action_space", "pyautogui")),
        "screen_size": (
            int(options.get("screen_width", 1920)),
            int(options.get("screen_height", 1080)),
        ),
        "headless": bool(options.get("headless", False)),
        "os_type": str(options.get("os_type", "Ubuntu")),
        "require_a11y_tree": observation_type in {"a11y_tree", "screenshot_a11y_tree", "som"},
    }
    optional_keys = {
        "region",
        "snapshot_name",
        "cache_dir",
        "require_terminal",
        "enable_proxy",
        "client_password",
        "vm_secret_mounts",
    }
    for key in optional_keys:
        if key in options:
            env_options[key] = options[key]
    return env_options


def _osworld_prompt(
    prompt: str,
    *,
    context: AgentContext,
    observation: dict[str, Any],
    previous_chars: int,
    previous_observation_chars: int,
) -> str:
    del previous_chars, previous_observation_chars
    parts = [
        "You are controlling an OSWorld Ubuntu desktop.",
        "Use the OSWorld pyautogui action space.",
        "Return one action: either executable Python in one fenced code block, or exactly DONE, FAIL, or WAIT.",
        "",
        f"Protocol: {context.protocol}; attempt {context.attempt_index} of {context.k}.",
        prompt.strip(),
    ]
    tree = observation.get("accessibility_tree")
    if tree:
        parts.extend(["", "Accessibility tree:", str(tree)[:12000]])
    terminal = observation.get("terminal")
    if terminal:
        parts.extend(["", "Terminal:", str(terminal)[-4000:]])
    return "\n".join(parts).strip() + "\n"


def _complete_for_observation(
    *,
    model_client: Any,
    prompt: str,
    observation: dict[str, Any],
    context: AgentContext,
    send_screenshot: bool,
) -> Any:
    screenshot = observation.get("screenshot")
    if send_screenshot and isinstance(screenshot, bytes) and callable(getattr(model_client, "complete_with_image", None)):
        return model_client.complete_with_image(prompt, screenshot, context=context)
    return model_client.complete(prompt, context=context)


def _extract_osworld_actions(text: str) -> list[Any]:
    raw = text.strip()
    if not raw:
        return []
    special = _extract_special_action(raw)
    if special is not None:
        return [special]
    fenced = re.findall(r"```(?:python|py|code)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        actions: list[str] = []
        for block in fenced:
            actions.extend(_extract_python_action_blocks(block))
        if actions:
            return actions
    return []


def _extract_special_action(text: str) -> str | None:
    stripped = text.strip()
    if stripped in {"WAIT", "DONE", "FAIL"}:
        return stripped
    fenced = re.fullmatch(r"```(?:text|plaintext)?\s*(WAIT|DONE|FAIL)\s*```", stripped, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).upper()
    return None


def _split_terminal_commands(block: str) -> list[str]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if lines and lines[-1] in {"WAIT", "DONE", "FAIL"}:
        code = "\n".join(lines[:-1]).strip()
        return ([code] if code else []) + [lines[-1]]
    return [block]


def _extract_python_action_blocks(block: str) -> list[str]:
    block = block.strip()
    if not block:
        return []
    special = _extract_special_action(block)
    if special is not None:
        return [special]
    return _split_terminal_commands(block)


def _compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if "screenshot" in observation:
        screenshot = observation.get("screenshot")
        compact["screenshot_bytes"] = len(screenshot) if isinstance(screenshot, bytes) else bool(screenshot)
    for key in ("instruction", "terminal", "accessibility_tree"):
        value = observation.get(key)
        if value is not None:
            compact[key] = str(value)[:2000]
    return compact


def _default_source_path() -> Path | None:
    return DEFAULT_SOURCE_PATH if DEFAULT_SOURCE_PATH.exists() else None


def _add_source_path(source_path: Path) -> None:
    resolved = str(source_path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def _desktop_env_class() -> Any:
    from desktop_env.desktop_env import DesktopEnv

    return DesktopEnv
