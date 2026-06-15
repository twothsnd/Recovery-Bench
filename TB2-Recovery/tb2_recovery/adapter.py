from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recovery_bench.config import AgentConfig, BenchmarkConfig, ModelConfig
from recovery_bench.io import dump_dataclass_json
from recovery_bench.types import (
    ActionRecord,
    AgentCapabilities,
    AgentContext,
    AgentRunResult,
    BenchmarkCapabilities,
    BenchmarkResult,
    StateSnapshot,
    Task,
    TaskOutcome,
)

from tb2_recovery.harbor_path import ensure_harbor_site_packages


ensure_harbor_site_packages()


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-").lower()
    return value or "unnamed"


def _compose_project_name(value: str) -> str:
    value = value.lower()
    if not re.match(r"^[a-z0-9]", value):
        value = "0" + value
    return re.sub(r"[^a-z0-9_-]", "-", value)


def _run(argv: list[str], *, check: bool = False, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(
            argv,
            124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=f"command timed out after {timeout}s",
        )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _read_task_toml(task_dir: Path) -> dict[str, Any]:
    return tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))


def _read_task_image(task_dir: Path) -> str:
    data = _read_task_toml(task_dir)
    image = data.get("environment", {}).get("docker_image")
    if not isinstance(image, str) or not image:
        raise ValueError(f"missing [environment].docker_image in {task_dir / 'task.toml'}")
    return image


def _patch_task_image(task_toml: Path, image: str) -> None:
    text = task_toml.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(?m)^(\s*docker_image\s*=\s*)["\'][^"\']+["\']',
        rf'\1"{image}"',
        text,
        count=1,
    )
    if n != 1:
        raise ValueError(f"could not patch docker_image in {task_toml}")
    task_toml.write_text(new_text, encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _resolve_path(value: Any, *, base: Path) -> Path | None:
    if value is None or value == "":
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else base / path


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items()}


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _with_llm_call_token_limit(options: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(options)
    llm_call_kwargs = dict(normalized.get("llm_call_kwargs") or {})
    if "max_tokens" not in llm_call_kwargs:
        max_tokens = _coerce_positive_int(normalized.get("max_tokens"))
        model_info = normalized.get("model_info")
        if max_tokens is None and isinstance(model_info, dict):
            max_tokens = _coerce_positive_int(model_info.get("max_output_tokens"))
        if max_tokens is not None:
            llm_call_kwargs["max_tokens"] = max_tokens
    if llm_call_kwargs:
        normalized["llm_call_kwargs"] = llm_call_kwargs
    return normalized


def _build_wheelhouse_policy(value: Any) -> bool | str:
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto"
    return _as_bool(value)


def _run_with_env(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=merged_env)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _docker_image_exists(image: str) -> bool:
    return _run(["docker", "image", "inspect", image]).returncode == 0


def _json_from_stdout(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"stdout": text}
    return data if isinstance(data, dict) else {"stdout_json": data}


def _ensure_harbor_importable(harbor_bin: str | None = None, site_packages: str | None = None) -> None:
    candidates: list[Path] = []
    if site_packages:
        candidates.append(Path(site_packages).expanduser())
    if harbor_bin:
        script = Path(harbor_bin).expanduser()
        if script.exists():
            first = script.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            if first.startswith("#!"):
                py = Path(first[2:].strip())
                candidates.append(
                    py.parent.parent
                    / "lib"
                    / f"python{sys.version_info.major}.{sys.version_info.minor}"
                    / "site-packages"
                )

    for candidate in candidates:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    try:
        import harbor  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "Could not import Harbor. Set benchmark.options.harbor_site_packages "
            "or run with the Harbor-compatible Python environment."
        ) from exc


@dataclass(slots=True)
class LocalOptimizationConfig:
    enabled: bool = True
    build_wheelhouse: bool | str = "auto"
    mount_wheelhouse: bool = True
    require_wheelhouse: bool = False
    prebake_images: bool = True
    mutate_original_images: bool = True
    pull_missing_images: bool = True
    docker_mirror_prefix: str = "docker.1panel.live"
    docker_pull_retries: int = 3
    docker_pull_total_timeout_sec: int = 1800
    optimized_image_prefix: str = "tb2-local-opt"
    wheelhouse_path: Path | None = None
    wheelhouse_container_path: str = "/opt/tb2/wheelhouse"
    build_wheelhouse_script: Path | None = None
    prebake_script: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def effective_wheelhouse(self, project_root: Path) -> Path:
        return self.wheelhouse_path or (project_root / "wheelhouse")

    def effective_build_script(self, project_root: Path) -> Path:
        return self.build_wheelhouse_script or (project_root / "scripts" / "build_wheelhouse.sh")

    def effective_prebake_script(self, project_root: Path) -> Path:
        return self.prebake_script or (project_root / "scripts" / "prebake_task_image.sh")


@dataclass(slots=True)
class VMSnapshotConfig:
    enabled: bool = False
    reset_command: str | None = None
    attempt_command: str | None = None
    restore_command: str | None = None
    discard_command: str | None = None
    clean_command: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = [
            name
            for name, value in {
                "vm_reset_command": self.reset_command,
                "vm_attempt_command": self.attempt_command,
                "vm_restore_command": self.restore_command,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Strict VM backend requires: {', '.join(missing)}")


@dataclass(slots=True)
class TB2Benchmark:
    name: str
    dataset_path: Path
    project_root: Path
    harbor_bin: str | None = None
    harbor_site_packages: str | None = None
    success_threshold: float = 1.0
    cleanup_recovery_images: bool = True
    state_backend: str = "docker_commit"
    local_optimization: LocalOptimizationConfig = field(default_factory=LocalOptimizationConfig)
    vm_snapshot: VMSnapshotConfig = field(default_factory=VMSnapshotConfig)
    session_id: str = field(default_factory=lambda: f"tb2-rb-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")

    current_task_id: str | None = None
    current_image: str | None = None
    original_images: dict[str, str] = field(default_factory=dict)
    optimized_images: dict[str, str] = field(default_factory=dict)
    owned_local_images: set[str] = field(default_factory=set)
    owned_recovery_images: set[str] = field(default_factory=set)
    owned_vm_snapshots: set[str] = field(default_factory=set)
    last_attempt_result: dict[str, Any] | None = None
    last_attempt_paths: dict[str, str] = field(default_factory=dict)
    last_pre_verifier_snapshot: StateSnapshot | None = None
    last_restored_snapshot: StateSnapshot | None = None
    strict_snapshot_expected: bool = False
    local_optimization_prepared: bool = False

    def __post_init__(self) -> None:
        self.dataset_path = self.dataset_path.resolve()
        self.project_root = self.project_root.resolve()
        self.state_backend = self.state_backend.strip().lower()
        if self.state_backend not in {"docker_commit", "strict_vm_command"}:
            raise ValueError(f"unsupported TB2 state_backend: {self.state_backend}")
        if self.state_backend == "strict_vm_command":
            self.vm_snapshot.enabled = True
        self.vm_snapshot.validate()
        if self.local_optimization.wheelhouse_path is not None:
            self.local_optimization.wheelhouse_path = self.local_optimization.wheelhouse_path.resolve()
        if self.local_optimization.build_wheelhouse_script is not None:
            self.local_optimization.build_wheelhouse_script = self.local_optimization.build_wheelhouse_script.resolve()
        if self.local_optimization.prebake_script is not None:
            self.local_optimization.prebake_script = self.local_optimization.prebake_script.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.raw_runs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def work_dir(self) -> Path:
        return self.project_root / "work" / self.session_id

    @property
    def raw_runs_dir(self) -> Path:
        return self.project_root / "runs" / "terminus2" / self.session_id

    @property
    def state_dir(self) -> Path:
        return self.project_root / "state" / self.session_id

    def list_tasks(self) -> list[str]:
        return sorted(p.name for p in self.dataset_path.iterdir() if (p / "task.toml").is_file())

    def load_task(self, task_id: str) -> Task:
        task_dir = self.dataset_path / task_id
        if not (task_dir / "task.toml").is_file():
            raise KeyError(f"Unknown TB2 task_id: {task_id}")
        prompt = (task_dir / "instruction.md").read_text(encoding="utf-8")
        metadata = {"task_dir": str(task_dir), "original_image": _read_task_image(task_dir)}
        return Task(task_id=task_id, prompt=prompt, metadata=metadata)

    def reset(self, task: Task) -> StateSnapshot:
        image = self.original_images.get(task.task_id)
        if image is None:
            image = _read_task_image(self.dataset_path / task.task_id)
            self.original_images[task.task_id] = image
        image = self._prepare_base_image(task, image)
        if self.state_backend == "strict_vm_command":
            self._reset_strict_vm(task=task, image=image)
        self.current_task_id = task.task_id
        self.current_image = image
        self.last_attempt_result = None
        self.last_attempt_paths = {}
        self.last_pre_verifier_snapshot = None
        self.last_restored_snapshot = None
        self.strict_snapshot_expected = False
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        payload = dict(snapshot.payload)
        self.current_task_id = str(payload["task_id"])
        self.current_image = str(payload.get("image") or self.original_images.get(self.current_task_id) or "")
        if payload.get("state_kind") in {"vm_snapshot", "strict_vm_snapshot"}:
            self._restore_vm_snapshot(payload)
            self.last_restored_snapshot = StateSnapshot(
                payload=payload,
                label=snapshot.label,
                metadata={**dict(snapshot.metadata), "restored": True},
            )
        else:
            self.last_restored_snapshot = None
        self.last_pre_verifier_snapshot = None
        self.strict_snapshot_expected = False
        return self.last_restored_snapshot or self.snapshot(label=snapshot.label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        if label and label.endswith("-before") and self.last_restored_snapshot is not None:
            return StateSnapshot(
                payload=dict(self.last_restored_snapshot.payload),
                label=label,
                metadata=dict(self.last_restored_snapshot.metadata),
            )
        if label and label.endswith("-after") and self.last_pre_verifier_snapshot is not None:
            payload = dict(self.last_pre_verifier_snapshot.payload)
            return StateSnapshot(payload=payload, label=label, metadata=dict(self.last_pre_verifier_snapshot.metadata))
        if label and label.endswith("-after") and self.strict_snapshot_expected:
            raise RuntimeError(
                "TB2 attempt finished without a pre-verifier state snapshot; "
                "recovery state would be invalid."
            )
        if self.current_task_id is None or self.current_image is None:
            raise RuntimeError("TB2Benchmark has no active task state.")
        return StateSnapshot(
            payload={
                "task_id": self.current_task_id,
                "image": self.current_image,
                "state_kind": "vm_live" if self.state_backend == "strict_vm_command" else "docker_image",
                "state_backend": self.state_backend,
            },
            label=label,
            metadata={
                "benchmark": self.name,
                "snapshot": "vm_live_reference" if self.state_backend == "strict_vm_command" else "docker_image_reference",
                "strict_point": "clean_vm_after_reset" if self.state_backend == "strict_vm_command" else "current_image",
                "local_optimization": self._local_optimization_metadata(),
            },
        )

    def agent_environment(self) -> "TB2Benchmark":
        return self

    def evaluate(self, task: Task) -> TaskOutcome:
        result = self.last_attempt_result or {}
        verifier_result = result.get("verifier_result")
        rewards = verifier_result.get("rewards", {}) if isinstance(verifier_result, dict) else {}
        reward = rewards.get("reward") if isinstance(rewards, dict) else None
        if reward is None:
            reward = result.get("reward", result.get("score"))
        score = float(reward) if isinstance(reward, int | float) else None
        explicit_success = result.get("success")
        success = bool(explicit_success) if isinstance(explicit_success, bool) else bool(score is not None and score >= self.success_threshold)
        return TaskOutcome(
            success=success,
            score=score,
            details={
                "reward": reward,
                "success_threshold": self.success_threshold,
                "trial_name": result.get("trial_name"),
                "trial_dir": self.last_attempt_paths.get("trial_dir"),
                "result_path": self.last_attempt_paths.get("result_path"),
                "trajectory_path": self.last_attempt_paths.get("trajectory_path"),
                "exception_info": result.get("exception_info"),
            },
        )

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        if self.state_backend == "docker_commit":
            return BenchmarkCapabilities(
                state_materialization="docker_commit_at_harbor_verification_start",
                state_snapshot="pre_verifier_main_container_filesystem_image",
                restore_strategy="patch_task_docker_image_to_committed_image",
                evaluator_isolation="official_verifier_runs_after_pre_verifier_commit",
                budget_reset="harbor_trial_per_attempt_timeout",
                official_invariance="official_tb2_task_and_harbor_trial_api",
                official_harness="harbor_trial_api",
                strict_recovery=False,
                limitations=(
                    "Docker commit preserves the Harbor main container filesystem only.",
                    "It does not preserve running processes, memory, sockets, anonymous runtime state, sidecar services, or Docker volumes.",
                    "This backend is a practical substitute while strict VM snapshot recovery is unavailable.",
                ),
                metadata={
                    "state_backend": self.state_backend,
                    "local_optimization": self._local_optimization_metadata(),
                    "vm_snapshot_enabled": False,
                },
            )
        return BenchmarkCapabilities(
            state_materialization="strict_vm_attempt_command_pre_score_snapshot",
            state_snapshot="vm_ram_disk_snapshot_before_official_scorer",
            restore_strategy="vm_restore_command_then_continue_next_terminus2_attempt",
            evaluator_isolation="attempt_command_scores_in_discardable_vm_branch",
            budget_reset="strict_vm_terminus2_attempt_command_per_attempt_budget",
            official_invariance="official_tb2_task_semantics_and_official_verifier_result",
            official_harness="strict_vm_terminus2_command",
            strict_recovery=True,
            limitations=(
                "Strictness depends on the configured VM runner honoring the contract: run Terminus2 in the VM, snapshot before scoring, score only on a disposable branch, and return the pre-score snapshot id.",
                "This backend requires reset_command, attempt_command, and restore_command.",
            ),
            metadata={
                "state_backend": self.state_backend,
                "local_optimization": self._local_optimization_metadata(),
                "vm_snapshot_enabled": self.vm_snapshot.enabled,
            },
        )

    def run_terminus2_attempt(
        self,
        task: Task,
        context: AgentContext,
        *,
        model_name: str,
        agent_options: dict[str, Any],
    ) -> AgentRunResult:
        if self.current_image is None:
            raise RuntimeError("No active TB2 Docker image. Did reset/restore run?")
        if self.state_backend == "docker_commit":
            return self._run_docker_commit_attempt(
                task,
                context,
                model_name=model_name,
                agent_options=agent_options,
            )
        return self._run_strict_vm_attempt(
            task,
            context,
            model_name=model_name,
            agent_options=agent_options,
        )

    def _run_docker_commit_attempt(
        self,
        task: Task,
        context: AgentContext,
        *,
        model_name: str,
        agent_options: dict[str, Any],
    ) -> AgentRunResult:
        _ensure_harbor_importable(self.harbor_bin or shutil.which("harbor"), self.harbor_site_packages)
        attempt_start_image = self.current_image or ""
        self.last_pre_verifier_snapshot = None
        self.strict_snapshot_expected = True
        attempt_dir = self.raw_runs_dir / task.task_id / context.protocol / f"attempt_{context.attempt_index}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        task_path = self._make_attempt_task_dir(task, context, attempt_start_image)
        memory_path = self._write_recovery_memory(attempt_dir, context, agent_options)

        trial_name = _safe_name(
            f"rb-{task.task_id}-{context.protocol}-a{context.attempt_index}-{uuid.uuid4().hex[:8]}"
        )
        result = self._run_harbor_trial(
            task=task,
            task_path=task_path,
            attempt_dir=attempt_dir,
            trial_name=trial_name,
            model_name=model_name,
            memory_path=memory_path,
            agent_options=agent_options,
            context=context,
        )
        result_dict = _jsonable(result)
        self.last_attempt_result = result_dict

        trial_dir = attempt_dir / "trials" / trial_name
        result_path = trial_dir / "result.json"
        trajectory_path = trial_dir / "agent" / "trajectory.json"
        self.last_attempt_paths = {
            "trial_dir": str(trial_dir),
            "result_path": str(result_path),
            "trajectory_path": str(trajectory_path) if trajectory_path.exists() else "",
            "task_path": str(task_path),
        }
        action = ActionRecord(
            action={
                "docker_commit_attempt": True,
                "harbor_trial": trial_name,
                "task_path": str(task_path),
                "attempt_start_image": attempt_start_image,
                "pre_verifier_snapshot_image": self.current_image,
            },
            observation={
                "result_path": str(result_path),
                "trajectory_path": str(trajectory_path) if trajectory_path.exists() else None,
            },
            metadata={
                "trial_name": trial_name,
                "trial_dir": str(trial_dir),
                "result_path": str(result_path),
                "trajectory_path": str(trajectory_path) if trajectory_path.exists() else None,
                "pre_verifier_snapshot": self.last_pre_verifier_snapshot.payload if self.last_pre_verifier_snapshot else None,
            },
        )
        return AgentRunResult(actions=(action,), metadata={"harbor_result": result_dict, "docker_commit_result": result_dict})

    def _run_strict_vm_attempt(
        self,
        task: Task,
        context: AgentContext,
        *,
        model_name: str,
        agent_options: dict[str, Any],
    ) -> AgentRunResult:
        if not self.vm_snapshot.attempt_command:
            raise RuntimeError("strict_vm_command backend requires vm_snapshot.attempt_command")

        attempt_start_image = self.current_image or ""
        self.last_pre_verifier_snapshot = None
        self.strict_snapshot_expected = True
        attempt_dir = self.raw_runs_dir / task.task_id / context.protocol / f"attempt_{context.attempt_index}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        task_path = self._make_attempt_task_dir(task, context, attempt_start_image)
        memory_path = self._write_recovery_memory(attempt_dir, context, agent_options)
        result_path = attempt_dir / "vm_attempt_result.json"

        variables = self._vm_attempt_variables(
            task=task,
            context=context,
            task_path=task_path,
            attempt_dir=attempt_dir,
            memory_path=memory_path,
            model_name=model_name,
            agent_options=agent_options,
            result_path=result_path,
        )
        command_result = self._run_command_template(
            self.vm_snapshot.attempt_command,
            variables=variables,
            env=self._vm_command_env(agent_options),
        )
        result = self._load_strict_vm_attempt_result(result_path, command_result)
        self.last_attempt_result = result

        snapshot_id = str(result.get("snapshot_id") or result.get("pre_score_snapshot_id") or "")
        if not snapshot_id:
            raise RuntimeError(
                "strict VM attempt did not return snapshot_id/pre_score_snapshot_id; "
                "cannot continue Recovery@k from an exact failed state."
            )
        self.owned_vm_snapshots.add(snapshot_id)
        self.last_pre_verifier_snapshot = StateSnapshot(
            payload={
                "task_id": task.task_id,
                "image": attempt_start_image,
                "state_kind": "strict_vm_snapshot",
                "state_backend": self.state_backend,
                "snapshot_id": snapshot_id,
                "snapshot_name": result.get("snapshot_name", snapshot_id),
                "attempt_index": context.attempt_index,
                "protocol": context.protocol,
                "result_path": str(result_path),
            },
            label=f"attempt-{context.attempt_index}-pre-score-vm",
            metadata={
                "benchmark": self.name,
                "snapshot": "strict_vm_snapshot",
                "strict_point": "before_official_scorer",
                "strict_recovery": True,
            },
        )

        trajectory_path = str(result.get("trajectory_path") or "")
        official_result_path = str(result.get("result_path") or result_path)
        self.last_attempt_paths = {
            "trial_dir": str(result.get("trial_dir") or attempt_dir),
            "result_path": official_result_path,
            "trajectory_path": trajectory_path,
            "task_path": str(task_path),
        }
        action = ActionRecord(
            action={
                "strict_vm_attempt": True,
                "task_path": str(task_path),
                "attempt_start_image": attempt_start_image,
                "pre_score_snapshot_id": snapshot_id,
            },
            observation={
                "result_path": official_result_path,
                "trajectory_path": trajectory_path or None,
            },
            metadata={
                "snapshot_id": snapshot_id,
                "result_path": official_result_path,
                "trajectory_path": trajectory_path or None,
                "pre_verifier_snapshot": self.last_pre_verifier_snapshot.payload,
                "command_result": command_result,
            },
        )
        return AgentRunResult(actions=(action,), metadata={"terminus2_result": result, "strict_vm_result": result})

    def _load_strict_vm_attempt_result(self, result_path: Path, command_result: dict[str, Any]) -> dict[str, Any]:
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"strict VM attempt result is not valid JSON: {result_path}") from exc
            if not isinstance(data, dict):
                raise RuntimeError(f"strict VM attempt result must be a JSON object: {result_path}")
            data.setdefault("command_result", command_result)
            return data
        return dict(command_result)

    def _vm_attempt_variables(
        self,
        *,
        task: Task,
        context: AgentContext,
        task_path: Path,
        attempt_dir: Path,
        memory_path: Path | None,
        model_name: str,
        agent_options: dict[str, Any],
        result_path: Path,
    ) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "session_id": self.session_id,
            "attempt_index": context.attempt_index,
            "protocol": context.protocol,
            "k": context.k,
            "task_path": task_path,
            "task_dir": task_path,
            "attempt_dir": attempt_dir,
            "result_path": result_path,
            "memory_path": memory_path or Path("/dev/null"),
            "model_name": model_name,
            "api_base": agent_options.get("api_base", ""),
            "openai_api_key": agent_options.get("openai_api_key", os.environ.get("OPENAI_API_KEY", "EMPTY")),
            "parser_name": agent_options.get("parser_name", "json"),
            "temperature": agent_options.get("temperature", 0.7),
            "image": self.current_image or "",
            "wheelhouse_path": self.local_optimization.effective_wheelhouse(self.project_root),
            "wheelhouse_container_path": self.local_optimization.wheelhouse_container_path,
        }

    def _prepare_base_image(self, task: Task, image: str) -> str:
        if not self.local_optimization.enabled:
            return image
        if not self.local_optimization_prepared:
            self._prepare_local_optimization_assets()
            self.local_optimization_prepared = True
        if not self.local_optimization.prebake_images:
            return image
        cached = self.optimized_images.get(image)
        if cached:
            return cached
        optimized = self._prebake_image(task, image)
        self.optimized_images[image] = optimized
        return optimized

    def _prepare_local_optimization_assets(self) -> None:
        if not self.local_optimization.enabled:
            return
        policy = self.local_optimization.build_wheelhouse
        should_build = policy is True or (policy == "auto" and not self._wheelhouse_ready())
        if should_build:
            script = self.local_optimization.effective_build_script(self.project_root)
            if not script.exists():
                raise FileNotFoundError(f"TB2 wheelhouse script not found: {script}")
            env = self._local_optimization_env()
            env.setdefault("TB2_WHEELHOUSE", str(self.local_optimization.effective_wheelhouse(self.project_root)))
            _run_with_env(["bash", str(script)], env=env, check=True)

    def _wheelhouse_ready(self) -> bool:
        wheelhouse = self.local_optimization.effective_wheelhouse(self.project_root)
        if not wheelhouse.exists():
            return False
        try:
            next(wheelhouse.rglob("*.whl"))
        except StopIteration:
            return False
        return True

    def _prebake_image(self, task: Task, image: str) -> str:
        del task
        script = self.local_optimization.effective_prebake_script(self.project_root)
        if not script.exists():
            raise FileNotFoundError(f"TB2 prebake script not found: {script}")
        self._ensure_image_available(image)

        target_image = image
        if not self.local_optimization.mutate_original_images:
            target_image = (
                f"{self.local_optimization.optimized_image_prefix.rstrip('/')}/"
                f"{_safe_name(image)}:prebaked"
            )
            if not _docker_image_exists(target_image):
                _run(["docker", "tag", image, target_image], check=True)

        env = self._local_optimization_env()
        env.setdefault("TB2_WHEELHOUSE", str(self.local_optimization.effective_wheelhouse(self.project_root)))
        _run_with_env(["bash", str(script), target_image], env=env, check=True)
        return target_image

    def _ensure_image_available(self, image: str) -> None:
        if _docker_image_exists(image):
            return
        if not self.local_optimization.pull_missing_images:
            raise RuntimeError(f"Docker image is missing and pull_missing_images=false: {image}")
        refs = []
        mirror = self.local_optimization.docker_mirror_prefix.strip("/")
        if mirror:
            refs.append(f"{mirror}/{image}")
        refs.append(image)
        last_error = ""
        for ref in refs:
            for attempt in range(1, max(1, self.local_optimization.docker_pull_retries) + 1):
                proc = _run(
                    ["docker", "pull", ref],
                    check=False,
                    timeout=self.local_optimization.docker_pull_total_timeout_sec,
                )
                if proc.returncode == 0:
                    if ref != image:
                        _run(["docker", "tag", ref, image], check=True)
                    return
                last_error = proc.stderr.strip() or proc.stdout.strip()
                if attempt < self.local_optimization.docker_pull_retries:
                    time.sleep(min(30, 5 * attempt))
        raise RuntimeError(f"could not pull Docker image {image}: {last_error}")

    def _local_optimization_env(self) -> dict[str, str]:
        env = dict(self.local_optimization.env)
        if self.local_optimization.mount_wheelhouse or self.local_optimization.build_wheelhouse:
            env.setdefault("TB2_WHEELHOUSE", str(self.local_optimization.effective_wheelhouse(self.project_root)))
            env.setdefault("TB2_WHEELHOUSE_IN_CONTAINER", self.local_optimization.wheelhouse_container_path)
        return env

    def _local_optimization_metadata(self) -> dict[str, Any]:
        cfg = self.local_optimization
        return {
            "enabled": cfg.enabled,
            "build_wheelhouse": cfg.build_wheelhouse,
            "mount_wheelhouse": cfg.mount_wheelhouse,
            "require_wheelhouse": cfg.require_wheelhouse,
            "prebake_images": cfg.prebake_images,
            "mutate_original_images": cfg.mutate_original_images,
            "pull_missing_images": cfg.pull_missing_images,
            "docker_mirror_prefix": cfg.docker_mirror_prefix,
            "docker_pull_retries": cfg.docker_pull_retries,
            "docker_pull_total_timeout_sec": cfg.docker_pull_total_timeout_sec,
            "wheelhouse_path": str(cfg.effective_wheelhouse(self.project_root)) if cfg.enabled else None,
            "optimized_image_prefix": cfg.optimized_image_prefix if cfg.enabled else None,
        }

    def _harbor_mounts(self) -> list[dict[str, Any]] | None:
        cfg = self.local_optimization
        if not cfg.enabled or not cfg.mount_wheelhouse:
            return None
        wheelhouse = cfg.effective_wheelhouse(self.project_root)
        if not wheelhouse.exists():
            if not cfg.require_wheelhouse:
                return None
            raise FileNotFoundError(
                f"TB2 wheelhouse path does not exist: {wheelhouse}. "
                "Run scripts/build_wheelhouse.sh or set build_wheelhouse=true."
            )
        return [
            {
                "type": "bind",
                "source": str(wheelhouse),
                "target": cfg.wheelhouse_container_path,
                "read_only": True,
            }
        ]

    def _harbor_environment_env(self) -> dict[str, str]:
        env = {}
        if self.local_optimization.enabled:
            env.update(self._local_optimization_env())
        return env

    def _run_command_template(
        self,
        command: str,
        *,
        variables: dict[str, Any],
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        formatted = command.format(**{key: str(value) for key, value in variables.items()})
        argv = shlex.split(formatted)
        proc = _run_with_env(argv, env=env or self.vm_snapshot.env, check=check)
        data = _json_from_stdout(proc)
        data.setdefault("returncode", proc.returncode)
        data.setdefault("stderr", proc.stderr.strip())
        return data

    def _vm_command_env(self, agent_options: dict[str, Any] | None = None) -> dict[str, str]:
        env = dict(self.vm_snapshot.env)
        env.update(self._local_optimization_env())
        if agent_options:
            env.setdefault("OPENAI_API_KEY", str(agent_options.get("openai_api_key", os.environ.get("OPENAI_API_KEY", "EMPTY"))))
            if agent_options.get("api_base"):
                env.setdefault("OPENAI_BASE_URL", str(agent_options["api_base"]))
        return env

    def _reset_strict_vm(self, *, task: Task, image: str) -> None:
        if not self.vm_snapshot.reset_command:
            raise RuntimeError("strict_vm_command backend requires vm_snapshot.reset_command")
        variables = {
            "task_id": task.task_id,
            "session_id": self.session_id,
            "task_path": self.dataset_path / task.task_id,
            "task_dir": self.dataset_path / task.task_id,
            "image": image,
            "wheelhouse_path": self.local_optimization.effective_wheelhouse(self.project_root),
            "wheelhouse_container_path": self.local_optimization.wheelhouse_container_path,
        }
        self._run_command_template(
            self.vm_snapshot.reset_command,
            variables=variables,
            env=self._vm_command_env(),
        )

    def _restore_vm_snapshot(self, payload: dict[str, Any]) -> None:
        if not self.vm_snapshot.restore_command:
            raise RuntimeError("vm_restore_command is not configured")
        snapshot_id = str(payload.get("snapshot_id") or "")
        if not snapshot_id:
            raise RuntimeError("VM snapshot payload is missing snapshot_id")
        variables = {
            "task_id": payload.get("task_id", ""),
            "session_id": self.session_id,
            "snapshot_id": snapshot_id,
            "snapshot_name": payload.get("snapshot_name", snapshot_id),
            "image": payload.get("image", ""),
        }
        self._run_command_template(self.vm_snapshot.restore_command, variables=variables)

    def _discard_vm_snapshot(self, snapshot_id: str) -> None:
        if not self.vm_snapshot.discard_command:
            return
        self._run_command_template(
            self.vm_snapshot.discard_command,
            variables={"session_id": self.session_id, "snapshot_id": snapshot_id, "snapshot_name": snapshot_id},
            check=False,
        )

    def _clean_strict_vm(self) -> None:
        if not self.vm_snapshot.clean_command:
            return
        self._run_command_template(
            self.vm_snapshot.clean_command,
            variables={"session_id": self.session_id},
            env=self._vm_command_env(),
            check=False,
        )

    def _make_attempt_task_dir(self, task: Task, context: AgentContext, image: str) -> Path:
        src = self.dataset_path / task.task_id
        dst_root = self.work_dir / task.task_id / context.protocol / f"attempt_{context.attempt_index}"
        dst_task = dst_root / task.task_id
        if dst_root.exists():
            shutil.rmtree(dst_root)
        dst_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst_task, symlinks=True)
        _patch_task_image(dst_task / "task.toml", image)
        return dst_task

    def _write_recovery_memory(
        self,
        attempt_dir: Path,
        context: AgentContext,
        agent_options: dict[str, Any],
    ) -> Path | None:
        if context.protocol != "recovery" or not context.previous_attempts:
            return None
        messages = _build_previous_attempt_chat_messages(context.previous_attempts)
        if messages:
            path = attempt_dir / "recovery_chat_messages.json"
            path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
            return path

        text = _format_previous_attempt_memory(context.previous_attempts)
        max_chars = int(agent_options.get("memory_max_chars", 0) or 0)
        if max_chars > 0 and len(text) > max_chars:
            text = "[TRUNCATED RECOVERY MEMORY]\n" + text[-max_chars:]
        path = attempt_dir / "recovery_memory.md"
        path.write_text(text, encoding="utf-8")
        return path

    def _run_harbor_trial(
        self,
        *,
        task: Task,
        task_path: Path,
        attempt_dir: Path,
        trial_name: str,
        model_name: str,
        memory_path: Path | None,
        agent_options: dict[str, Any],
        context: AgentContext,
    ) -> Any:
        from harbor.models.trial.config import (
            AgentConfig as HarborAgentConfig,
            EnvironmentConfig as HarborEnvironmentConfig,
            TaskConfig as HarborTaskConfig,
            TrialConfig,
            VerifierConfig as HarborVerifierConfig,
        )
        from harbor.trial.hooks import TrialEvent
        from harbor.trial.trial import Trial

        api_key = str(agent_options.get("openai_api_key", os.environ.get("OPENAI_API_KEY", "EMPTY")))
        old_api_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = api_key

        kwargs: dict[str, Any] = {}
        if "parser_name" in agent_options:
            kwargs["parser_name"] = agent_options["parser_name"]
        if "temperature" in agent_options:
            kwargs["temperature"] = float(agent_options["temperature"])
        api_base = agent_options.get("api_base")
        if api_base:
            kwargs["api_base"] = str(api_base)
        if memory_path is not None:
            if memory_path.suffix == ".json":
                kwargs["recovery_chat_messages_path"] = str(memory_path)
            else:
                kwargs["recovery_memory_path"] = str(memory_path)
        for key in (
            "max_turns",
            "max_thinking_tokens",
            "reasoning_effort",
            "enable_summarize",
            "proactive_summarization_threshold",
            "model_info",
            "trajectory_config",
            "tmux_pane_width",
            "tmux_pane_height",
            "store_all_messages",
            "record_terminal_session",
            "interleaved_thinking",
            "suppress_max_turns_warning",
            "use_responses_api",
            "llm_backend",
            "llm_kwargs",
            "llm_call_kwargs",
            "dynamic_max_tokens",
            "output_token_safety_margin",
        ):
            if key in agent_options:
                kwargs[key] = agent_options[key]

        # Terminus2 runs LLM calls in the Harbor host process, not inside the
        # task tmux shell. Passing these values as Harbor agent env makes
        # Terminus2 forward them with `tmux new-session -e`, which fails on
        # older task images with tmux 3.1c. Keep model config in kwargs and
        # host OPENAI_API_KEY only.
        agent_env = {}

        config = TrialConfig(
            task=HarborTaskConfig(path=task_path),
            trial_name=trial_name,
            trials_dir=attempt_dir / "trials",
            agent=HarborAgentConfig(
                import_path="tb2_recovery.terminus2_memory_agent:RecoveryTerminus2",
                model_name=model_name,
                override_timeout_sec=agent_options.get("override_timeout_sec"),
                override_setup_timeout_sec=agent_options.get("override_setup_timeout_sec"),
                max_timeout_sec=agent_options.get("max_timeout_sec"),
                kwargs=kwargs,
                env=agent_env,
            ),
            environment=HarborEnvironmentConfig(
                # Harbor's delete=True path runs `docker compose down --rmi all`,
                # which removes the committed recovery image before the next
                # attempt can restore from it. Plain `down` still cleans the
                # trial containers while preserving the snapshot tag.
                delete=False,
                mounts_json=self._harbor_mounts(),
                env=self._harbor_environment_env(),
            ),
            verifier=HarborVerifierConfig(
                disable=False,
                override_timeout_sec=agent_options.get("override_verifier_timeout_sec"),
                max_timeout_sec=agent_options.get("max_verifier_timeout_sec"),
            ),
        )

        async def _create_and_run() -> Any:
            trial = await Trial.create(config)
            trial.add_hook(
                TrialEvent.VERIFICATION_START,
                lambda event: self._pre_verifier_snapshot_hook(
                    event,
                    task=task,
                    context=context,
                    trial_name=trial_name,
                ),
            )
            return await trial.run()

        try:
            return asyncio.run(_create_and_run())
        finally:
            if old_api_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_api_key

    async def _pre_verifier_snapshot_hook(
        self,
        event: Any,
        *,
        task: Task,
        context: AgentContext,
        trial_name: str,
    ) -> None:
        del event
        container_id = self._find_main_container(trial_name)
        tag = f"tb2-recovery/{_safe_name(task.task_id)}:{_safe_name(self.session_id)}-a{context.attempt_index}-{uuid.uuid4().hex[:8]}"
        _run(["docker", "commit", container_id, tag], check=True)
        self.owned_recovery_images.add(tag)
        self.current_image = tag
        self.last_pre_verifier_snapshot = StateSnapshot(
            payload={
                "task_id": task.task_id,
                "image": tag,
                "state_kind": "docker_image",
                "state_backend": self.state_backend,
                "source_trial": trial_name,
                "attempt_index": context.attempt_index,
                "protocol": context.protocol,
                "container_id": container_id,
            },
            label=f"attempt-{context.attempt_index}-pre-verifier",
            metadata={
                "benchmark": self.name,
                "snapshot": "docker_commit",
                "strict_point": "harbor_VERIFICATION_START",
                "strict_recovery": False,
            },
        )

    def _find_main_container(self, trial_name: str) -> str:
        project = _compose_project_name(trial_name)
        proc = _run(
            [
                "docker",
                "ps",
                "-a",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=main",
            ],
            check=True,
        )
        ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if not ids:
            raise RuntimeError(f"Could not find Harbor main container for compose project {project!r}")
        return ids[0]

    def close(self) -> None:
        for snapshot_id in sorted(self.owned_vm_snapshots):
            self._discard_vm_snapshot(snapshot_id)
        if self.state_backend == "strict_vm_command":
            self._clean_strict_vm()
        if self.cleanup_recovery_images:
            for image in sorted(self.owned_recovery_images):
                _run(["docker", "rmi", "-f", image], check=False)
            for image in sorted(self.owned_local_images):
                _run(["docker", "rmi", "-f", image], check=False)
        shutil.rmtree(self.work_dir, ignore_errors=True)


@dataclass(slots=True)
class TB2TerminusAgent:
    name: str
    model_name: str
    options: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        task: Task,
        prompt: str,
        environment: TB2Benchmark,
        context: AgentContext,
    ) -> AgentRunResult:
        del prompt
        return environment.run_terminus2_attempt(
            task,
            context,
            model_name=self.model_name,
            agent_options=self.options,
        )

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            memory_mode="terminus2_native_chat_history_from_previous_attempt_episodes",
            retry_memory_reset="new_harbor_trial_without_recovery_memory",
            recovery_memory="previous_attempt_terminus2_prompt_response_pairs_loaded_into_chat_messages",
            trajectory_export="harbor_terminus2_trajectory",
            official_agent="Harbor Terminus2",
            limitations=(
                "Docker-commit state recovery does not preserve processes, sockets, anonymous runtime state, or Docker volumes.",
            ),
        )


def _build_previous_attempt_chat_messages(previous_attempts: tuple[Any, ...]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for attempt in previous_attempts:
        for action in attempt.agent_result.actions:
            trajectory_path = action.metadata.get("trajectory_path")
            if not trajectory_path:
                continue
            path = Path(str(trajectory_path))
            if path.exists():
                messages.extend(_read_terminus2_episode_chat_messages(path))
        if attempt.outcome is not None:
            status = getattr(attempt.status, "value", str(attempt.status))
            details = _compact_official_outcome_details(attempt.outcome.details)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Recovery-Bench attempt boundary: attempt {attempt.attempt_index} ended with "
                        f"status={status}. The official evaluator returned success={attempt.outcome.success}. "
                        f"Details: {json.dumps(details, ensure_ascii=False, default=str)}\n"
                        "Continue the original task from the current environment state in the next attempt."
                    ),
                }
            )
    return messages


def _read_terminus2_episode_chat_messages(trajectory_path: Path) -> list[dict[str, str]]:
    agent_dir = trajectory_path.parent
    episode_dirs = sorted(
        (path for path in agent_dir.glob("episode-*") if path.is_dir()),
        key=_episode_sort_key,
    )
    messages: list[dict[str, str]] = []
    for episode_dir in episode_dirs:
        prompt_path = episode_dir / "prompt.txt"
        response_path = episode_dir / "response.txt"
        if not prompt_path.exists():
            continue
        try:
            prompt = prompt_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        messages.append({"role": "user", "content": prompt})
        if not response_path.exists():
            continue
        try:
            response = response_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        messages.append({"role": "assistant", "content": response})
    return messages


def _episode_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"episode-(\d+)$", path.name)
    if not match:
        return (10**9, path.name)
    return (int(match.group(1)), path.name)


def _compact_official_outcome_details(details: Any) -> Any:
    if not isinstance(details, dict):
        return details
    keep: dict[str, Any] = {}
    for key in ("reward", "success_threshold", "trial_name", "result_path", "trajectory_path"):
        if key in details:
            keep[key] = details[key]
    exception_info = details.get("exception_info")
    if isinstance(exception_info, dict):
        keep["exception_info"] = {
            key: exception_info[key]
            for key in ("exception_type", "exception_message", "occurred_at")
            if key in exception_info
        }
    return keep


def _format_previous_attempt_memory(previous_attempts: tuple[Any, ...]) -> str:
    blocks: list[str] = []
    for attempt in previous_attempts:
        status = getattr(attempt.status, "value", str(attempt.status))
        blocks.append(f"## Attempt {attempt.attempt_index}")
        blocks.append(f"Status: {status}")
        if attempt.outcome is not None:
            blocks.append(f"Official evaluator success: {attempt.outcome.success}")
            blocks.append("Official evaluator details:")
            blocks.append(json.dumps(attempt.outcome.details, ensure_ascii=False, indent=2, default=str))
        for action in attempt.agent_result.actions:
            trajectory_path = action.metadata.get("trajectory_path")
            result_path = action.metadata.get("result_path")
            if result_path:
                blocks.append(f"Attempt result path: {result_path}")
            if trajectory_path:
                blocks.append(f"Terminus2 trajectory path: {trajectory_path}")
                path = Path(str(trajectory_path))
                if path.exists():
                    blocks.append("Visible Terminus2 transcript for recovery memory:")
                    blocks.append(_read_visible_trajectory_transcript_for_memory(path))
        if attempt.agent_result.error:
            blocks.append(f"Agent error: {attempt.agent_result.error}")
        blocks.append("")
    return "\n".join(blocks).strip() + "\n"


_RECOVERY_MEMORY_STRIPPED_KEYS = {
    "reasoning",
    "reasoning_content",
}


def _read_visible_trajectory_transcript_for_memory(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    sanitized = _strip_hidden_reasoning(data)
    return _format_visible_trajectory_transcript(sanitized)


def _strip_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_hidden_reasoning(item)
            for key, item in value.items()
            if key not in _RECOVERY_MEMORY_STRIPPED_KEYS
        }
    if isinstance(value, list):
        return [_strip_hidden_reasoning(item) for item in value]
    return value


def _format_visible_trajectory_transcript(data: Any) -> str:
    if not isinstance(data, dict):
        return _format_memory_value(data)
    steps = data.get("steps")
    if not isinstance(steps, list):
        return _format_memory_value(data)

    lines: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id")
        source = step.get("source", "unknown")
        prefix = f"[{source}"
        if step_id is not None:
            prefix += f" step {step_id}"
        prefix += "]"

        message = step.get("message")
        if message not in (None, ""):
            lines.append(prefix)
            lines.append(str(message).strip())

        tool_calls = step.get("tool_calls")
        if tool_calls:
            lines.append(f"{prefix} tool_calls")
            lines.append(_format_memory_value(tool_calls))

        observation = step.get("observation")
        observation_text = _format_observation_for_memory(observation)
        if observation_text:
            lines.append(f"{prefix} observation")
            lines.append(observation_text)

    return "\n\n".join(part for part in lines if part).strip()


def _format_observation_for_memory(observation: Any) -> str:
    if not isinstance(observation, dict):
        return _format_memory_value(observation) if observation not in (None, "") else ""
    results = observation.get("results")
    if not isinstance(results, list):
        return _format_memory_value(observation)
    contents: list[str] = []
    for result in results:
        if isinstance(result, dict):
            content = result.get("content")
            if content not in (None, ""):
                contents.append(str(content).strip())
        elif result not in (None, ""):
            contents.append(str(result).strip())
    return "\n".join(contents).strip()


def _format_memory_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_benchmark(
    config: BenchmarkConfig | None = None,
    task_ids: tuple[str, ...] = (),
) -> TB2Benchmark:
    del task_ids
    options = dict(config.options if config else {})
    dataset_path = Path(str(config.dataset_path or options.pop("dataset_path", "external/terminal-bench-2"))) if config else Path("external/terminal-bench-2")
    project_root = Path(str(options.pop("project_root", "TB2-Recovery")))
    local_raw = dict(options.pop("local_optimization", {}) or {})
    vm_raw = dict(options.pop("vm_snapshot", {}) or {})

    def local_option(name: str, default: Any = None) -> Any:
        return local_raw.pop(name, options.pop(f"local_optimization_{name}", default))

    def vm_option(name: str, default: Any = None) -> Any:
        return vm_raw.pop(name, options.pop(f"vm_{name}", options.pop(f"vm_snapshot_{name}", default)))

    local_optimization = LocalOptimizationConfig(
        enabled=_as_bool(local_option("enabled", True), default=True),
        build_wheelhouse=_build_wheelhouse_policy(local_option("build_wheelhouse", "auto")),
        mount_wheelhouse=_as_bool(local_option("mount_wheelhouse", True), default=True),
        require_wheelhouse=_as_bool(local_option("require_wheelhouse", False)),
        prebake_images=_as_bool(local_option("prebake_images", True), default=True),
        mutate_original_images=_as_bool(local_option("mutate_original_images", True), default=True),
        pull_missing_images=_as_bool(local_option("pull_missing_images", True), default=True),
        docker_mirror_prefix=str(local_option("docker_mirror_prefix", "docker.1panel.live")),
        docker_pull_retries=int(local_option("docker_pull_retries", 3)),
        docker_pull_total_timeout_sec=int(local_option("docker_pull_total_timeout_sec", 1800)),
        optimized_image_prefix=str(local_option("optimized_image_prefix", "tb2-local-opt")),
        wheelhouse_path=_resolve_path(local_option("wheelhouse_path", None), base=project_root),
        wheelhouse_container_path=str(local_option("wheelhouse_container_path", "/opt/tb2/wheelhouse")),
        build_wheelhouse_script=_resolve_path(local_option("build_wheelhouse_script", None), base=project_root),
        prebake_script=_resolve_path(local_option("prebake_script", None), base=project_root),
        env=_str_dict(local_option("env", {})),
    )
    vm_snapshot = VMSnapshotConfig(
        enabled=_as_bool(vm_option("enabled", False)),
        reset_command=vm_option("reset_command", None),
        attempt_command=vm_option("attempt_command", None),
        restore_command=vm_option("restore_command", None),
        discard_command=vm_option("discard_command", None),
        clean_command=vm_option("clean_command", None),
        env=_str_dict(vm_option("env", {})),
    )
    return TB2Benchmark(
        name=config.name if config else "tb2",
        dataset_path=dataset_path,
        project_root=project_root,
        harbor_bin=options.pop("harbor_bin", shutil.which("harbor")),
        harbor_site_packages=options.pop("harbor_site_packages", None),
        success_threshold=float(options.pop("success_threshold", 1.0)),
        cleanup_recovery_images=_as_bool(options.pop("cleanup_recovery_images", True), default=True),
        state_backend=str(options.pop("state_backend", "docker_commit")),
        local_optimization=local_optimization,
        vm_snapshot=vm_snapshot,
    )


def build_agent(
    model_config: ModelConfig | None = None,
    agent_config: AgentConfig | None = None,
) -> TB2TerminusAgent:
    options = dict(agent_config.options if agent_config else {})
    options = _with_llm_call_token_limit(options)
    model_name = model_config.name if model_config else "openai/Qwen3.5-9B"
    return TB2TerminusAgent(
        name=agent_config.name if agent_config else "terminus-2",
        model_name=model_name,
        options=options,
    )
