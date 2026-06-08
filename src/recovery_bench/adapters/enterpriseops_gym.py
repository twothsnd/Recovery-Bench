from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
import re
import sys
import threading
import time
import uuid
import zipfile
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


DEFAULT_DOMAIN_ENDPOINTS = {
    "teams": ("gym-teams-mcp", "http://localhost:8002"),
    "csm": ("sn-csm-server", "http://localhost:8001"),
    "email": ("gym-email-mcp", "http://localhost:8004"),
    "itsm": ("gym-itsm-mcp", "http://localhost:8006"),
    "calendar": ("gym-calendar", "http://localhost:8003"),
    "hr": ("sn-hr-internal", "http://localhost:8008"),
    "drive": ("gym-google-drive-mcp", "http://localhost:8009"),
}
KNOWN_MODES = {"oracle", "plus_5_tools", "plus_10_tools", "plus_15_tools"}
KNOWN_DOMAINS = set(DEFAULT_DOMAIN_ENDPOINTS) | {"hybrid"}


def enterpriseops_gym_dependency_status() -> tuple[bool, str]:
    source_path = _default_source_path()
    if source_path is None:
        return False, "EnterpriseOps-Gym official source checkout is missing."
    try:
        import httpx  # noqa: F401
    except Exception as exc:
        return False, f"httpx import failed: {type(exc).__name__}: {exc}"
    return (
        True,
        "EnterpriseOps-Gym official source is present. Runtime also requires task configs and running MCP servers.",
    )


def build_enterpriseops_gym_benchmark(
    *,
    source_path: Path | None = None,
    configs_folder: Path | None = None,
    domain: str = "teams",
    mode: str = "oracle",
    hf_dataset: str = "ServiceNow-AI/EnterpriseOps-Gym",
    task_ids: tuple[str, ...] = (),
    options: dict[str, Any] | None = None,
) -> "EnterpriseOpsGymBenchmarkAdapter":
    source_path = source_path or _default_source_path()
    available, reason = enterpriseops_gym_dependency_status()
    if not available and source_path is None:
        raise NotImplementedError(reason)
    return EnterpriseOpsGymBenchmarkAdapter(
        source_path=source_path,
        configs_folder=configs_folder,
        domain=domain,
        mode=mode,
        hf_dataset=hf_dataset,
        task_ids=task_ids,
        options=dict(options or {}),
    )


@dataclass(slots=True)
class EnterpriseOpsGymBenchmarkAdapter(BenchmarkAdapter):
    """EnterpriseOps-Gym adapter over official MCP servers and SQL verifiers."""

    source_path: Path | None = None
    configs_folder: Path | None = None
    domain: str = "teams"
    mode: str = "oracle"
    hf_dataset: str = "ServiceNow-AI/EnterpriseOps-Gym"
    task_ids: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    name: str = "enterpriseops-gym"
    _task_index: dict[str, Path | dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _current_task: Task | None = field(default=None, init=False, repr=False)
    _current_config: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _runtime_gyms: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _clients: dict[str, "_MCPClient"] = field(default_factory=dict, init=False, repr=False)
    _available_tools: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _tool_to_gym: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _last_agent_result: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _last_model_client: Any = field(default=None, init=False, repr=False)
    _task_index_error: str | None = field(default=None, init=False, repr=False)

    def load_task(self, task_id: str) -> Task:
        config = self._load_task_config(task_id)
        prompt = str(config.get("user_prompt", "")).strip()
        if not prompt:
            prompt = str(config.get("task", task_id)).strip()
        metadata = {
            "domain": config.get("domain", self.domain),
            "mode": self.mode,
            "config": config,
        }
        return Task(task_id=task_id, prompt=prompt, metadata=metadata)

    def reset(self, task: Task) -> StateSnapshot:
        self._cleanup_live_databases()
        self._current_task = task
        self._current_config = dict(task.metadata["config"])
        self._last_agent_result = {}
        self._last_model_client = None
        self._runtime_gyms = []
        self._clients = {}
        self._available_tools = []
        self._tool_to_gym = {}

        for gym_config in self._task_gym_configs(self._current_config):
            runtime_gym = dict(gym_config)
            seed_path = self._resolve_seed_database_file(str(runtime_gym.get("seed_database_file", "")))
            runtime_gym["seed_database_file"] = str(seed_path)
            runtime_gym["database_id"] = self._create_database_from_seed(str(runtime_gym["mcp_server_url"]), seed_path)
            self._runtime_gyms.append(runtime_gym)

        if not self._uses_official_flow():
            self._connect_clients()
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
        gyms = payload.get("gyms")
        if not isinstance(gyms, list):
            raise RuntimeError("EnterpriseOps-Gym recovery snapshot does not contain live gym database handles.")
        self._runtime_gyms = [dict(gym) for gym in gyms]
        if not self._uses_official_flow():
            self._connect_clients()
        return self.snapshot(label=snapshot.label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        if self._current_task is None:
            raise RuntimeError("EnterpriseOps-Gym benchmark has not been reset yet.")
        return StateSnapshot(
            payload={
                "task_id": self._current_task.task_id,
                "domain": self.domain,
                "mode": self.mode,
                "restore_strategy": "live-mcp-database-handles",
                "gyms": [dict(gym) for gym in self._runtime_gyms],
            },
            label=label,
            metadata={"benchmark": self.name, "domain": self.domain, "mode": self.mode},
        )

    def agent_environment(self) -> Any:
        if self._current_task is None or self._current_config is None:
            raise RuntimeError("EnterpriseOps-Gym benchmark has not been reset yet.")
        if self._uses_official_flow():
            return EnterpriseOpsGymOfficialAgentEnvironment(
                config=self._current_config,
                runtime_gyms=[dict(gym) for gym in self._runtime_gyms],
                source_path=self.source_path,
                adapter_options=dict(self.options),
                state_sink=self._record_agent_state,
            )
        return EnterpriseOpsGymAgentEnvironment(
            config=self._current_config,
            clients=self._clients,
            available_tools=self._available_tools,
            tool_to_gym=self._tool_to_gym,
            adapter_options=dict(self.options),
            state_sink=self._record_agent_state,
        )

    def evaluate(self, task: Task) -> TaskOutcome:
        if self._current_config is None:
            raise RuntimeError("EnterpriseOps-Gym benchmark has not been reset yet.")
        official_result = self._last_agent_result.get("official_run_result")
        if isinstance(official_result, dict):
            summary = official_result.get("verification_summary") or {}
            total = int(summary.get("total") or 0)
            passed = int(summary.get("passed") or 0)
            success = bool(official_result.get("overall_success"))
            return TaskOutcome(
                success=success,
                score=(passed / total) if total else 0.0,
                details={
                    "verification_results": official_result.get("verification_results", {}),
                    "verification_summary": summary,
                    "tools_used": official_result.get("tools_used", []),
                    "official_run_result": official_result,
                },
            )

        verifier_results = {}
        verifiers = self._current_config.get("verifiers") or []
        for index, verifier in enumerate(verifiers):
            name = verifier.get("name") or f"verifier_{index + 1}"
            verifier_results[name] = self._execute_verifier(verifier)
        success = bool(verifier_results) and all(result.get("passed") for result in verifier_results.values())
        passed = sum(1 for result in verifier_results.values() if result.get("passed"))
        total = len(verifier_results)
        return TaskOutcome(
            success=success,
            score=(passed / total) if total else 0.0,
            details={
                "verification_results": verifier_results,
                "verification_summary": {"passed": passed, "total": total},
                "tools_used": self._last_agent_result.get("tools_used", []),
            },
        )

    def list_tasks(self) -> list[str]:
        if self.task_ids:
            return list(self.task_ids)
        self._ensure_task_index()
        return sorted(self._task_index)

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        return BenchmarkCapabilities(
            state_materialization="live_mcp_database_handles",
            state_snapshot="live_handle",
            restore_strategy="live-mcp-database-handles",
            evaluator_isolation="official_verifier_assumed_non_mutating",
            budget_reset="per_attempt_full",
            official_invariance="official_runner" if self._uses_official_flow() else "wrapped_mcp_tools",
            official_harness="enterpriseops-gym",
            strict_recovery=False,
            limitations=(
                "Recovery state currently reuses live database handles rather than strict database snapshots.",
            ),
        )

    def close(self) -> None:
        self._cleanup_live_databases()
        self._clients = {}

    def _record_agent_state(self, result: dict[str, Any], model_client: Any) -> None:
        self._last_agent_result = result
        self._last_model_client = model_client

    def _uses_official_flow(self) -> bool:
        mode = str(self.options.get("execution_mode") or self.options.get("adapter_mode") or "official").lower()
        return mode not in {"legacy", "legacy-text-json", "text-json", "json-mcp"}

    def _load_task_config(self, task_id: str) -> dict[str, Any]:
        self._ensure_task_index()
        if task_id not in self._task_index:
            raise KeyError(f"Unknown EnterpriseOps-Gym task id: {task_id}")
        entry = self._task_index[task_id]
        if isinstance(entry, dict):
            return _normalize_task_config(entry)
        return _normalize_task_config(json.loads(entry.read_text(encoding="utf-8")))

    def _ensure_task_index(self) -> None:
        if self._task_index:
            return
        if self.configs_folder is not None:
            self._index_config_folder(self.configs_folder)
        if not self._task_index:
            cache_dir = _path_option(self.options.get("task_cache_dir"))
            if cache_dir is not None:
                self._index_config_folder(cache_dir)
        if not self._task_index:
            self._load_hf_task_index()
        if not self._task_index:
            reason = (
                f" Last HF load error: {self._task_index_error}."
                if self._task_index_error
                else ""
            )
            raise NotImplementedError(
                "EnterpriseOps-Gym task configs are not available locally. "
                "Set benchmark.options.configs_folder or install datasets/pyarrow so the adapter can load "
                f"the official Hugging Face dataset {self.hf_dataset!r}.{reason}"
            )

    def _index_config_folder(self, folder: Path) -> None:
        if not folder.exists():
            return
        for path in sorted(folder.rglob("*.json")):
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            config = _normalize_task_config(config)
            if not self._config_matches_selection(config, path):
                continue
            task_id = str(config.get("task_id") or path.stem)
            self._task_index[task_id] = path

    def _config_matches_selection(self, config: dict[str, Any], path: Path) -> bool:
        path_parts = set(path.parts)
        config_domain = str(config.get("domain") or "").strip() or _first_path_match(path_parts, KNOWN_DOMAINS)
        config_mode = str(config.get("mode") or "").strip() or _first_path_match(path_parts, KNOWN_MODES)
        if config_domain and config_domain != self.domain:
            return False
        if config_mode and config_mode != self.mode:
            return False
        return True

    def _load_hf_task_index(self) -> None:
        try:
            from datasets import load_dataset
        except Exception as exc:
            self._task_index_error = f"datasets import failed: {type(exc).__name__}: {exc}"
            return
        try:
            dataset = load_dataset(self.hf_dataset, self.mode, split=self.domain, trust_remote_code=False)
        except TypeError:
            dataset = load_dataset(self.hf_dataset, self.mode, split=self.domain)
        except Exception as exc:
            self._task_index_error = f"datasets.load_dataset failed: {type(exc).__name__}: {exc}"
            return
        json_fields = {"gym_servers_config", "verifiers"}
        hf_only_fields = {"domain"}
        for row in dataset:
            task_id = str(row.get("task_id") or f"{self.domain}_{len(self._task_index)}")
            config = {}
            for key, value in row.items():
                if key in hf_only_fields:
                    continue
                if key in json_fields and isinstance(value, str):
                    value = json.loads(value)
                config[key] = value
            config.setdefault("task_id", task_id)
            config.setdefault("domain", self.domain)
            config.setdefault("mode", self.mode)
            self._task_index[task_id] = config

    def _task_gym_configs(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        gym_configs = config.get("gym_servers_config")
        if isinstance(gym_configs, list) and gym_configs:
            return [self._apply_mcp_server_overrides(dict(item)) for item in gym_configs]
        if config.get("mcp_server_url"):
            return [
                self._apply_mcp_server_overrides(
                    {
                        "mcp_server_name": config.get("mcp_server_name") or "default_gym",
                        "mcp_server_url": config["mcp_server_url"],
                        "mcp_endpoint": config.get("mcp_endpoint", "/mcp"),
                        "seed_database_file": config.get("seed_database_file", ""),
                        "auth_config": config.get("auth_config"),
                        "context": config.get("context", {}),
                    }
                )
            ]
        server_name, url = DEFAULT_DOMAIN_ENDPOINTS.get(self.domain, (self.domain, ""))
        if not url:
            raise RuntimeError(f"No default EnterpriseOps-Gym MCP endpoint for domain: {self.domain}")
        return [
            self._apply_mcp_server_overrides(
                {
                    "mcp_server_name": server_name,
                    "mcp_server_url": url,
                    "mcp_endpoint": "/mcp",
                    "seed_database_file": config.get("seed_database_file", ""),
                    "auth_config": config.get("auth_config"),
                    "context": config.get("context", {}),
                }
            )
        ]

    def _apply_mcp_server_overrides(self, gym_config: dict[str, Any]) -> dict[str, Any]:
        overrides = self.options.get("mcp_server_url_overrides") or self.options.get("mcp_server_urls") or {}
        if not isinstance(overrides, dict):
            return gym_config
        keys = [
            str(gym_config.get("mcp_server_name") or ""),
            str(gym_config.get("domain") or ""),
            self._domain_from_seed_path(str(gym_config.get("seed_database_file") or "")),
            self._domain_from_url(str(gym_config.get("mcp_server_url") or "")),
        ]
        for key in keys:
            if key and key in overrides:
                gym_config["mcp_server_url"] = str(overrides[key])
                return gym_config
        return gym_config

    def _domain_from_seed_path(self, value: str) -> str:
        parts = set(Path(value).parts)
        return _first_path_match(parts, KNOWN_DOMAINS)

    def _domain_from_url(self, value: str) -> str:
        for domain, (_, url) in DEFAULT_DOMAIN_ENDPOINTS.items():
            if value.rstrip("/") == url.rstrip("/"):
                return domain
        return ""

    def _connect_clients(self) -> None:
        clients: dict[str, _MCPClient] = {}
        tools: list[dict[str, Any]] = []
        tool_to_gym: dict[str, str] = {}
        for gym in self._runtime_gyms:
            name = str(gym["mcp_server_name"])
            client = _MCPClient(
                base_url=str(gym["mcp_server_url"]),
                mcp_endpoint=str(gym.get("mcp_endpoint") or "/mcp"),
                database_id=str(gym["database_id"]),
                auth_config=gym.get("auth_config"),
                context=gym.get("context") or {},
            )
            client.initialize()
            clients[name] = client
            for tool in client.list_tools():
                tool_name = str(tool.get("name", ""))
                if not tool_name or tool_name in tool_to_gym:
                    continue
                enhanced = dict(tool)
                enhanced["_mcp_server_name"] = name
                enhanced["_database_id"] = str(gym["database_id"])
                tools.append(enhanced)
                tool_to_gym[tool_name] = name

        selected = set(self._current_config.get("selected_tools") or []) if self._current_config else set()
        restricted = set(self._current_config.get("restricted_tools") or []) if self._current_config else set()
        if selected:
            tools = [tool for tool in tools if tool.get("name") in selected]
            tool_to_gym = {name: gym for name, gym in tool_to_gym.items() if name in selected}
        if restricted:
            tools = [tool for tool in tools if tool.get("name") not in restricted]
            tool_to_gym = {name: gym for name, gym in tool_to_gym.items() if name not in restricted}

        self._clients = clients
        self._available_tools = tools
        self._tool_to_gym = tool_to_gym

    def _execute_verifier(self, verifier: dict[str, Any]) -> dict[str, Any]:
        verifier_type = verifier.get("verifier_type")
        config = verifier.get("validation_config") or {}
        gym_name = verifier.get("gym_name")
        if verifier_type == "database_state":
            return self._execute_database_state_verifier(config, gym_name)
        if verifier_type == "tool_execution":
            return self._execute_tool_execution_verifier(config)
        if verifier_type == "response_check":
            return self._execute_response_check_verifier(config, gym_name)
        return {"passed": False, "error": f"Unsupported verifier type: {verifier_type}"}

    def _execute_database_state_verifier(self, config: dict[str, Any], gym_name: str | None) -> dict[str, Any]:
        query = config.get("query")
        if not query:
            return {"passed": False, "error": "No SQL query provided"}
        sql_result = self._execute_sql_query(str(query), gym_name)
        if not sql_result.get("success"):
            return {"passed": False, "error": sql_result.get("error"), "query": query}
        actual = _extract_sql_value(sql_result.get("result"))
        comparison = _compare_values(actual, config.get("expected_value"), str(config.get("comparison_type", "equals")))
        return {
            "passed": comparison["passed"],
            "expected": config.get("expected_value"),
            "actual": actual,
            "comparison_type": config.get("comparison_type", "equals"),
            "query": query,
            "details": comparison.get("details"),
        }

    def _execute_response_check_verifier(self, config: dict[str, Any], gym_name: str | None) -> dict[str, Any]:
        query = config.get("sql_query")
        comparison_prompt = config.get("comparison_prompt")
        if not query or not comparison_prompt:
            return {"passed": False, "error": "Missing sql_query or comparison_prompt"}
        sql_result = self._execute_sql_query(str(query), gym_name)
        if not sql_result.get("success"):
            return {"passed": False, "error": sql_result.get("error"), "query": query}
        if self._last_model_client is None:
            return {
                "passed": False,
                "error": "response_check verifier requires the model client captured during agent execution",
                "query": query,
            }
        judge_prompt = "\n".join(
            [
                "Grade whether the assistant response satisfies the verifier.",
                "Return only JSON with keys score, passed, and explanation.",
                "",
                "Verifier prompt:",
                str(comparison_prompt),
                "",
                "SQL result:",
                json.dumps(sql_result.get("result"), default=str),
                "",
                "Assistant response:",
                str(self._last_agent_result.get("final_response", "")),
            ]
        )
        response = self._last_model_client.complete(
            judge_prompt,
            context=AgentContext(
                benchmark=self.name,
                task_id=self._current_task.task_id if self._current_task else "",
                protocol="judge",
                attempt_index=0,
                k=1,
            ),
        )
        parsed = _extract_json_object(response.text)
        score = _coerce_float(parsed.get("score")) if isinstance(parsed, dict) else None
        minimum = _coerce_float(config.get("minimum_comparison_value", 7)) or 7.0
        passed = bool(parsed.get("passed")) if isinstance(parsed, dict) and "passed" in parsed else bool(score is not None and score >= minimum)
        return {
            "passed": passed,
            "score": score,
            "minimum": minimum,
            "judge": parsed if isinstance(parsed, dict) else {"raw": response.text},
            "query": query,
        }

    def _execute_tool_execution_verifier(self, config: dict[str, Any]) -> dict[str, Any]:
        expected = list(config.get("selected_tools") or [])
        minimum = int(config.get("minimum_tool_calls", 1))
        tool_results = self._last_agent_result.get("tool_results", [])
        called = [str(item.get("tool_name")) for item in tool_results]
        missing = [tool for tool in expected if tool not in called]
        return {
            "passed": len(missing) == 0 and len(called) >= minimum,
            "selected_tools": expected,
            "tools_called": called,
            "missing_tools": missing,
            "minimum_tool_calls": minimum,
            "actual_tool_calls": len(called),
        }

    def _execute_sql_query(self, query: str, gym_name: str | None) -> dict[str, Any]:
        client = self._client_for_gym(gym_name)
        return client.sql_query(query)

    def _client_for_gym(self, gym_name: str | None) -> "_MCPClient":
        if gym_name and gym_name in self._clients:
            return self._clients[gym_name]
        if not self._clients:
            raise RuntimeError("EnterpriseOps-Gym MCP clients are not connected.")
        return next(iter(self._clients.values()))

    def _resolve_seed_database_file(self, value: str) -> Path:
        if not value:
            raise RuntimeError("EnterpriseOps-Gym task config is missing seed_database_file.")
        raw = Path(value)
        if raw.is_absolute() and raw.exists():
            return raw
        roots = [Path.cwd()]
        if self.configs_folder is not None:
            roots.append(self.configs_folder)
        dbs_root = _path_option(self.options.get("dbs_root"))
        if dbs_root is not None:
            roots.append(dbs_root)
        if self.source_path is not None:
            roots.extend([self.source_path, self._ensure_unpacked_dbs()])
        for root in roots:
            candidate = root / raw
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"EnterpriseOps-Gym seed database file not found: {value}")

    def _ensure_unpacked_dbs(self) -> Path:
        if self.source_path is None:
            return Path.cwd()
        target = self.source_path.parent / "data"
        marker = target / "Domain Wise DBs and Task-DB Mappings"
        if marker.exists():
            return target
        archive = self.source_path / "gym_dbs.zip"
        if archive.exists() and bool(self.options.get("auto_unzip_dbs", True)):
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(target)
        return target

    def _create_database_from_seed(self, gym_url: str, seed_path: Path) -> str:
        if self._uses_official_flow():
            database_id = _official_create_database_from_file(self.source_path, gym_url, seed_path)
            if not database_id:
                raise RuntimeError(f"EnterpriseOps-Gym official database creation returned no database_id for {seed_path}")
            return str(database_id)
        return _create_database_from_file(gym_url, seed_path)

    def _cleanup_live_databases(self) -> None:
        if not bool(self.options.get("delete_databases", True)):
            return
        for gym in self._runtime_gyms:
            database_id = gym.get("database_id")
            url = gym.get("mcp_server_url")
            if database_id and url:
                _delete_database(str(url), str(database_id))
        self._runtime_gyms = []


@dataclass(slots=True)
class EnterpriseOpsGymOfficialAgentEnvironment:
    """Recovery Bench wrapper around EnterpriseOps-Gym's official tool-calling flow."""

    config: dict[str, Any]
    runtime_gyms: list[dict[str, Any]]
    source_path: Path | None = None
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
        try:
            official_result = _run_async(
                self._run_official_attempt(
                    task=task,
                    prompt=prompt,
                    model_client=model_client,
                    context=context,
                    options=run_options,
                )
            )
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return AgentRunResult(
                metadata={
                    "bridge": "enterpriseops-gym-official",
                    "official_flow": True,
                },
                error=f"EnterpriseOps-Gym official flow failed: {type(exc).__name__}: {exc}",
            )

        result = {
            "final_response": official_result.get("model_response", ""),
            "tool_results": official_result.get("tool_results", []),
            "tools_used": official_result.get("tools_used", []),
            "official_run_result": official_result,
        }
        if callable(self.state_sink):
            self.state_sink(result, model_client)

        actions = _action_records_from_official_flow(official_result)
        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "enterpriseops-gym-official",
                "official_flow": True,
                "max_iterations": official_result.get("max_iterations"),
                "steps": len(actions),
                "tools_used": official_result.get("tools_used", []),
                "verification_summary": official_result.get("verification_summary", {}),
            },
        )

    async def _run_official_attempt(
        self,
        *,
        task: Task,
        prompt: str,
        model_client: Any,
        context: AgentContext,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        BenchmarkExecutor = _official_benchmark_executor(self.source_path)
        user_prompt = _official_user_prompt(self.config, task=task, prompt=prompt, context=context)
        trajectory_prefix_attempts = 0
        if context.protocol == "recovery" and context.attempt_index > 1:
            trajectory_prefix_attempts = len(context.previous_attempts)
            user_prompt = prefix_with_previous_attempt_trajectory(
                user_prompt,
                context.previous_attempts,
                max_chars=int(options.get("recovery_trajectory_max_chars", 0)),
                max_observation_chars=int(options.get("recovery_trajectory_max_observation_chars", 0)),
            )
        benchmark_config = _official_benchmark_config(
            self.config,
            user_prompt=user_prompt,
        )
        llm_config = _official_llm_config(model_client, options)
        max_iterations = int(options.get("max_steps", options.get("max_iterations", 50)))
        executor = BenchmarkExecutor(
            config=benchmark_config,
            llm_config=llm_config,
            orchestrator_kwargs={"max_iterations": max_iterations},
            config_path=str(self.source_path or Path.cwd()),
        )

        # Match the official disclosure path: tools are discovered through the
        # official MCP client and passed to the official LLMClient.bind_tools().
        # The database id is attached after tool discovery, mirroring
        # BenchmarkExecutor.execute_single_run().
        runtime_by_name = {str(gym["mcp_server_name"]): gym for gym in self.runtime_gyms}
        executor.gym_configs = [
            {**_official_gym_config_without_database_id(gym), "database_id": ""}
            for gym in self.runtime_gyms
        ]
        with _patch_official_llm_endpoint(self.source_path):
            await executor.initialize()
        _attach_runtime_database_ids(executor, runtime_by_name)

        orchestrator = executor.orchestrator_class(
            llm_client=executor.llm_client,
            mcp_clients=executor.mcp_clients,
            tool_to_server_mapping=executor.tool_to_server_mapping,
            available_tools=executor.available_tools,
            config=executor.config,
            **executor.orchestrator_kwargs,
        )
        started_at = time.time()
        task_result = await orchestrator.execute()
        verification_results = await executor._run_verifiers(task_result)
        total = len(verification_results)
        passed = sum(1 for result in verification_results.values() if result.get("passed", False))
        official_result = {
            "run_number": context.attempt_index,
            "execution_time_ms": int((time.time() - started_at) * 1000),
            "max_iterations": max_iterations,
            "model_response": task_result.get("final_response"),
            "conversation_flow": task_result.get("conversation_flow", []),
            "tools_used": task_result.get("tools_used", []),
            "tool_results": task_result.get("tool_results", []),
            "verification_results": verification_results,
            "verification_summary": {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": (passed / total) if total else 0.0,
            },
            "overall_success": all(result.get("passed", False) for result in verification_results.values()),
            "official_metadata": orchestrator.get_result_metadata(),
            "total_tools_available": len(executor.available_tools),
            "prompt_source": "recovery_prompt" if context.protocol == "recovery" and context.attempt_index > 1 else "official_user_prompt",
            "trajectory_prefix_attempts": trajectory_prefix_attempts,
            "effective_user_prompt": user_prompt,
            "effective_user_prompt_chars": len(user_prompt),
            "effective_user_prompt_has_trajectory": "Previous failed attempt trajectory:" in user_prompt,
        }
        official_result.update(orchestrator.get_result_metadata())
        return official_result


@dataclass(slots=True)
class EnterpriseOpsGymAgentEnvironment:
    config: dict[str, Any]
    clients: dict[str, "_MCPClient"]
    available_tools: list[dict[str, Any]]
    tool_to_gym: dict[str, str]
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
        max_steps = int(run_options.get("max_steps", 50))
        actions: list[ActionRecord] = []
        tool_results: list[dict[str, Any]] = []
        final_response = ""
        observations: list[str] = []

        for step in range(1, max_steps + 1):
            model_prompt = self._build_model_prompt(prompt, observations, run_options)
            response = model_client.complete(model_prompt, context=context)
            final_response = response.text
            action_payloads = _extract_enterpriseops_actions(response.text)
            if not action_payloads:
                actions.append(
                    ActionRecord(
                        action=response.text,
                        observation=None,
                        metadata={"step": step, "parse_error": True, "model_response": response.text},
                    )
                )
                break
            stop_after_step = False
            for action_index, action_payload in enumerate(action_payloads, start=1):
                if action_payload.get("final_answer") is not None or action_payload.get("action") == "final":
                    final_response = str(action_payload.get("final_answer", response.text))
                    actions.append(
                        ActionRecord(
                            action=action_payload,
                            observation=None,
                            metadata={
                                "step": step,
                                "action_index": action_index,
                                "final": True,
                                "model_response": response.text,
                            },
                        )
                    )
                    stop_after_step = True
                    break
                tool_name = str(action_payload.get("tool_name") or action_payload.get("name") or "")
                arguments = action_payload.get("arguments") or action_payload.get("args") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if tool_name not in self.tool_to_gym:
                    observation = {"success": False, "error": f"Unknown tool: {tool_name}"}
                    gym_name = ""
                else:
                    gym_name = self.tool_to_gym[tool_name]
                    observation = self.clients[gym_name].call_tool(tool_name, arguments)
                    observation["gym_server"] = gym_name
                    tool_results.append({"tool_name": tool_name, "arguments": arguments, "result": observation, "gym_server": gym_name})
                observations.append(json.dumps({"tool_name": tool_name, "observation": observation}, default=str))
                actions.append(
                    ActionRecord(
                        action=action_payload,
                        observation=observation,
                        metadata={
                            "step": step,
                            "action_index": action_index,
                            "model_response": response.text,
                        },
                    )
                )
            if stop_after_step:
                break

        result = {
            "final_response": final_response,
            "tool_results": tool_results,
            "tools_used": sorted({item["tool_name"] for item in tool_results}),
        }
        if callable(self.state_sink):
            self.state_sink(result, model_client)
        return AgentRunResult(
            actions=tuple(actions),
            metadata={
                "bridge": "enterpriseops-gym-json-mcp",
                "steps": len(actions),
                "tools_used": result["tools_used"],
            },
        )

    def _build_model_prompt(self, recovery_prompt: str, observations: list[str], options: dict[str, Any]) -> str:
        max_tool_chars = int(options.get("max_tool_chars", 60000))
        tools_json = json.dumps(
            _compact_tools(self.available_tools, schema_mode=str(options.get("tool_schema_mode", "full"))),
            indent=2,
            default=str,
        )
        if len(tools_json) > max_tool_chars:
            tools_json = tools_json[:max_tool_chars] + "\n...TRUNCATED..."
        parts = [
            str(self.config.get("system_prompt", "")).strip(),
            "",
            recovery_prompt.strip(),
            "",
            "Available tools:",
            tools_json,
            "",
            "Previous observations:",
            "\n".join(observations[-int(options.get("observation_window", 8)):]) or "None",
            "",
            "Return exactly one JSON object. To call a tool: {\"tool_name\": \"...\", \"arguments\": {...}}. "
            "When complete: {\"final_answer\": \"...\"}.",
        ]
        return "\n".join(part for part in parts if part is not None)


class _MCPClient:
    def __init__(
        self,
        *,
        base_url: str,
        mcp_endpoint: str = "/mcp",
        database_id: str = "",
        auth_config: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mcp_endpoint = mcp_endpoint
        self.database_id = database_id
        self.auth_config = auth_config or {}
        self.context = context or {}
        self._request_id = 1
        self._session_id: str | None = None

    def initialize(self) -> None:
        self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "recovery-bench", "version": "0.1.0"},
            },
        )
        self._send_notification("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        data = self._send_request("tools/list", {})
        return list(data.get("result", {}).get("tools", []))

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        data = self._send_request("tools/call", {"name": tool_name, "arguments": arguments})
        return {"success": True, "result": data.get("result"), "error": data.get("error")}

    def sql_query(self, query: str) -> dict[str, Any]:
        import httpx

        headers = self._headers()
        payload = {"query": query, "database_id": self.database_id}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(f"{self.base_url}/api/sql-runner", json=payload, headers=headers)
                response.raise_for_status()
                return {"success": True, "result": response.json()}
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{self.base_url}{self.mcp_endpoint}", json=payload, headers=self._headers())
            if "mcp-session-id" in response.headers:
                self._session_id = response.headers["mcp-session-id"]
            response.raise_for_status()
            data = response.json()
        if "error" in data and data["error"]:
            raise RuntimeError(f"MCP {method} failed: {data['error']}")
        return data

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        import httpx

        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{self.base_url}{self.mcp_endpoint}", json=payload, headers=self._headers())
            response.raise_for_status()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        if self.database_id:
            headers["x-database-id"] = self.database_id
        auth_type = self.auth_config.get("type")
        token = self.auth_config.get("token")
        if auth_type and token:
            header = self.auth_config.get("header_name", "Authorization")
            headers[header] = f"Bearer {token}" if auth_type == "bearer" else str(token)
        for key, value in self.context.items():
            header_key = key if str(key).lower().startswith("x-") else f"x-{str(key).lower().replace('_', '-')}"
            headers[header_key] = str(value)
        return headers


def _create_database_from_file(gym_url: str, sql_file_path: Path) -> str:
    import httpx

    sql_content = sql_file_path.read_text(encoding="utf-8")
    database_id = f"rb_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"
    payload = {
        "database_id": database_id,
        "name": f"Recovery Bench {database_id}",
        "description": f"Auto-created from {sql_file_path.name}",
        "sql_content": sql_content,
    }
    timeout = max(1200, int(120 + len(sql_content) / 102400))
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{gym_url.rstrip('/')}/api/seed-database", json=payload, headers={"Content-Type": "application/json"})
        response.raise_for_status()
    return database_id


def _delete_database(gym_url: str, database_id: str) -> None:
    try:
        import httpx

        with httpx.Client(timeout=30.0) as client:
            client.request("DELETE", f"{gym_url.rstrip('/')}/api/delete-database", json={"database_id": database_id})
    except Exception:
        return


def _official_create_database_from_file(source_path: Path | None, gym_url: str, seed_path: Path) -> str | None:
    _add_source_path(source_path)
    from benchmark.mcp_client import create_database_from_file

    return create_database_from_file(gym_url.rstrip("/"), str(seed_path))


def _official_benchmark_executor(source_path: Path | None):
    _add_source_path(source_path)
    from benchmark.executor import BenchmarkExecutor

    return BenchmarkExecutor


def _official_benchmark_config(config: dict[str, Any], *, user_prompt: str):
    from benchmark.models import BenchmarkConfig

    return BenchmarkConfig(
        system_prompt=str(config.get("system_prompt", "")),
        user_prompt=user_prompt,
        verifiers=list(config.get("verifiers") or []),
        number_of_runs=1,
        gym_servers_config=list(config.get("gym_servers_config") or []) or None,
        mcp_server_url=config.get("mcp_server_url"),
        mcp_endpoint=config.get("mcp_endpoint", "/mcp"),
        database_id=config.get("database_id"),
        context=config.get("context") or {},
        auth_config=config.get("auth_config"),
        selected_tools=list(config.get("selected_tools") or []),
        restricted_tools=list(config.get("restricted_tools") or []),
        temperature=float(config.get("temperature", 0.0) or 0.0),
        max_tokens=int(config.get("max_tokens", 4096) or 4096),
        reset_database_between_runs=bool(config.get("reset_database_between_runs", True)),
    )


def _official_llm_config(model_client: Any, options: dict[str, Any]):
    from benchmark.models import LLMConfig

    client_options = dict(getattr(model_client, "options", {}) or {})
    provider = str(getattr(model_client, "provider", "") or "").lower()
    model = str(getattr(model_client, "model", "") or "")
    max_tokens = int(
        client_options.get("max_tokens")
        or client_options.get("max_output_tokens")
        or options.get("max_tokens")
        or 4096
    )
    temperature = float(client_options.get("temperature") or options.get("temperature") or 0.0)
    top_p = client_options.get("top_p", options.get("top_p"))
    reasoning = client_options.get("reasoning")
    effort = client_options.get("effort") or (reasoning.get("effort") if isinstance(reasoning, dict) else None)

    if provider == "anthropic":
        official_provider = str(client_options.get("enterpriseops_provider") or "anthropic")
        api_key = _anthropic_api_key(client_options) or ""
        api_endpoint = _anthropic_base_url(client_options, default=None) or ""
    elif provider == "openai":
        api_endpoint = _openai_base_url(client_options) or ""
        official_provider = str(
            client_options.get("enterpriseops_provider")
            or client_options.get("llm_provider")
            or ("vllm" if api_endpoint else "openai")
        )
        api_key = _openai_api_key(client_options) or os.environ.get("OPENAI_API_KEY") or "not-needed"
    elif provider in {"gemini", "google"}:
        official_provider = "google"
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        api_endpoint = str(client_options.get("base_url") or "")
    else:
        official_provider = str(client_options.get("enterpriseops_provider") or provider)
        api_key = str(client_options.get("api_key") or "")
        api_endpoint = str(client_options.get("base_url") or "")

    return LLMConfig(
        llm_provider=official_provider,
        llm_model=model,
        llm_api_key=api_key,
        llm_api_endpoint=api_endpoint,
        llm_api_version=str(client_options.get("api_version") or client_options.get("api-version") or ""),
        llm_region=client_options.get("region"),
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        effort=effort,
        reasoning=reasoning if isinstance(reasoning, dict) else None,
    )


def _official_user_prompt(config: dict[str, Any], *, task: Task, prompt: str, context: AgentContext) -> str:
    if context.protocol == "recovery" and context.attempt_index > 1:
        return prompt.strip()
    return str(config.get("user_prompt") or task.prompt)


def _official_gym_config_without_database_id(gym: dict[str, Any]) -> dict[str, Any]:
    return {
        "mcp_server_name": gym["mcp_server_name"],
        "mcp_server_url": gym["mcp_server_url"],
        "seed_database_file": gym.get("seed_database_file", ""),
        "mcp_endpoint": gym.get("mcp_endpoint", "/mcp"),
        "auth_config": gym.get("auth_config"),
        "context": gym.get("context", {}),
    }


def _attach_runtime_database_ids(executor: Any, runtime_by_name: dict[str, dict[str, Any]]) -> None:
    for gym_config in getattr(executor, "gym_configs", []) or []:
        runtime_gym = runtime_by_name.get(str(gym_config.get("mcp_server_name") or ""))
        if runtime_gym is not None:
            gym_config["database_id"] = str(runtime_gym.get("database_id") or "")
    for gym_name, client in getattr(executor, "mcp_clients", {}).items():
        runtime_gym = runtime_by_name.get(str(gym_name))
        if runtime_gym is not None:
            client.database_id = str(runtime_gym.get("database_id") or "")


@contextmanager
def _patch_official_llm_endpoint(source_path: Path | None):
    """Let EnterpriseOps-Gym's official clients honor llm_api_endpoint.

    The official LLMClient already stores `api_endpoint`, but its Anthropic and
    OpenAI branches do not pass it into the LangChain client constructors. This
    patch is scoped to initialization and only changes transport endpoint
    selection; prompts, tool discovery, orchestration, and verification stay on
    the official path.
    """

    _add_source_path(source_path)
    try:
        from benchmark import llm_client as llm_client_module
    except Exception:
        yield
        return

    LLMClient = getattr(llm_client_module, "LLMClient", None)
    original = getattr(LLMClient, "_initialize_llm", None)
    if not callable(original):
        yield
        return

    def patched_initialize(self: Any) -> None:
        if getattr(self, "provider", None) == "anthropic" and getattr(self, "custom_api_endpoint", None):
            from langchain_anthropic import ChatAnthropic

            kwargs: dict[str, Any] = {
                "model": self.model,
                "anthropic_api_key": self.api_key,
                "base_url": str(self.custom_api_endpoint).rstrip("/"),
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if getattr(self, "top_p", None) is not None:
                kwargs["top_p"] = self.top_p
            self.llm = ChatAnthropic(**kwargs)
            return
        if getattr(self, "provider", None) == "openai" and getattr(self, "custom_api_endpoint", None):
            from langchain_openai import ChatOpenAI

            kwargs = {
                "model": self.model,
                "openai_api_key": self.api_key or "not-needed",
                "openai_api_base": str(self.custom_api_endpoint).rstrip("/"),
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if getattr(self, "top_p", None) is not None:
                kwargs["top_p"] = self.top_p
            self.llm = ChatOpenAI(**kwargs)
            return
        original(self)

    setattr(LLMClient, "_initialize_llm", patched_initialize)
    try:
        yield
    finally:
        setattr(LLMClient, "_initialize_llm", original)


def _action_records_from_official_flow(official_result: dict[str, Any]) -> list[ActionRecord]:
    actions: list[ActionRecord] = []
    for index, item in enumerate(official_result.get("conversation_flow", []), start=1):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "ai_message":
            tool_calls = item.get("tool_calls") or []
            if tool_calls:
                for tool_call in tool_calls:
                    actions.append(
                        ActionRecord(
                            action={"type": "official_tool_call", **dict(tool_call)},
                            observation=None,
                            metadata={
                                "flow_index": index,
                                "content": item.get("content", ""),
                                "usage_metadata": item.get("usage_metadata", {}),
                                "response_metadata": item.get("response_metadata", {}),
                            },
                        )
                    )
            elif item.get("content"):
                actions.append(
                    ActionRecord(
                        action={"type": "official_ai_message", "content": item.get("content", "")},
                        observation=None,
                        metadata={
                            "flow_index": index,
                            "usage_metadata": item.get("usage_metadata", {}),
                            "response_metadata": item.get("response_metadata", {}),
                        },
                    )
                )
        elif item_type == "tool_result":
            actions.append(
                ActionRecord(
                    action={"type": "official_tool_result", "tool_name": item.get("tool_name")},
                    observation=item.get("result"),
                    metadata={"flow_index": index, "gym_server": item.get("gym_server")},
                )
            )
    return actions


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result_box["result"] = asyncio.run(coro)
        except BaseException as exc:
            error_box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("result")


def _normalize_task_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    for key in ("gym_servers_config", "verifiers"):
        value = result.get(key)
        if isinstance(value, str):
            result[key] = json.loads(value)
    result.setdefault("number_of_runs", 1)
    result.setdefault("verifiers", [])
    result.setdefault("context", {})
    return result


def _compact_tools(tools: list[dict[str, Any]], *, schema_mode: str = "full") -> list[dict[str, Any]]:
    compact = []
    for tool in tools:
        input_schema = tool.get("inputSchema") or tool.get("input_schema")
        if schema_mode in {"params", "parameters", "compact"}:
            input_schema = _compact_input_schema(input_schema)
        elif schema_mode in {"none", "names"}:
            input_schema = None
        compact.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "inputSchema": input_schema,
            }
        )
    return compact


def _compact_input_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    compact_properties: dict[str, Any] = {}
    if isinstance(properties, dict):
        for name, value in properties.items():
            if not isinstance(value, dict):
                compact_properties[name] = value
                continue
            compact_properties[name] = {
                key: value[key]
                for key in ("type", "description", "enum", "items")
                if key in value
            }
    return {
        "type": schema.get("type", "object"),
        "required": schema.get("required", []),
        "properties": compact_properties,
    }


def _extract_json_object(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    if not stripped.startswith("{"):
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match:
            stripped = match.group(0)
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _extract_enterpriseops_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for payload in _extract_json_objects(text):
        action = _normalize_enterpriseops_action(payload)
        if action is not None:
            actions.append(action)
    for payload in _extract_tool_call_blocks(text):
        action = _normalize_enterpriseops_action(payload)
        if action is not None:
            actions.append(action)
    for payload in _extract_xml_invocations(text):
        action = _normalize_enterpriseops_action(payload)
        if action is not None:
            actions.append(action)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        key = json.dumps(action, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


def _normalize_enterpriseops_action(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("final_answer") is not None or payload.get("action") == "final":
        return payload
    tool_name = payload.get("tool_name") or payload.get("name") or payload.get("tool")
    arguments = payload.get("arguments") or payload.get("args") or payload.get("tool_input") or {}
    if not tool_name:
        return None
    if not isinstance(arguments, dict):
        arguments = {}
    return {"tool_name": str(tool_name), "arguments": arguments}


def _extract_json_objects(text: str) -> list[Any]:
    stripped = text.strip()
    objects: list[Any] = []
    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    candidates = fenced_blocks if fenced_blocks else [stripped]
    for candidate in candidates:
        for raw_object in _iter_balanced_json_objects(candidate):
            try:
                objects.append(json.loads(raw_object))
            except Exception:
                continue
    if objects:
        return objects
    single = _extract_json_object(text)
    return [single] if isinstance(single, dict) else []


def _iter_balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None
    return objects


def _extract_tool_call_blocks(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    blocks = re.findall(r"\[TOOL_CALL\](.*?)\[/TOOL_CALL\]", text, flags=re.DOTALL | re.IGNORECASE)
    for block in blocks:
        normalized = _normalize_tool_call_block_syntax(block)
        for raw_object in _iter_balanced_json_objects(normalized):
            try:
                payloads.append(json.loads(raw_object))
            except Exception:
                continue
        yamlish = _parse_tool_call_block(block)
        if yamlish is not None:
            payloads.append(yamlish)
    return payloads


def _normalize_tool_call_block_syntax(block: str) -> str:
    stripped = block.strip()
    return re.sub(
        r"(^|[{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(?:=>|=)\s*",
        r'\1"\2": ',
        stripped,
    )


def _parse_tool_call_block(block: str) -> dict[str, Any] | None:
    tool_match = re.search(r"(?:tool_name|tool)\s*(?::|=>|=)\s*\"?([A-Za-z0-9_.:-]+)\"?", block, flags=re.IGNORECASE)
    if tool_match is None:
        return None
    arguments: dict[str, Any] = {}
    params_match = re.search(r"(?:arguments|args|parameters)\s*(?::|=>|=)\s*", block, flags=re.DOTALL | re.IGNORECASE)
    if params_match is not None:
        for raw_object in _iter_balanced_json_objects(block[params_match.end() :]):
            try:
                parsed = json.loads(raw_object)
            except Exception:
                continue
            if isinstance(parsed, dict):
                arguments = parsed
                break
    return {"tool_name": tool_match.group(1), "arguments": arguments}


def _extract_xml_invocations(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for match in re.finditer(r"<invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</invoke>", text, flags=re.DOTALL | re.IGNORECASE):
        tool_name = match.group(1)
        body = match.group(2)
        arguments: dict[str, Any] = {}
        for param in re.finditer(r"<parameter\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</parameter>", body, flags=re.DOTALL | re.IGNORECASE):
            value = param.group(2).strip()
            try:
                arguments[param.group(1)] = json.loads(value)
            except Exception:
                arguments[param.group(1)] = value
        payloads.append({"tool_name": tool_name, "arguments": arguments})
    return payloads


def _extract_sql_value(result: Any) -> Any:
    if isinstance(result, dict):
        for key in ("rows", "data", "result", "results"):
            value = result.get(key)
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict) and first:
                    return next(iter(first.values()))
                return first
        if len(result) == 1:
            return next(iter(result.values()))
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict) and first:
            return next(iter(first.values()))
        return first
    return result


def _compare_values(actual: Any, expected: Any, comparison_type: str) -> dict[str, Any]:
    if comparison_type == "equals":
        return {"passed": actual == expected}
    if comparison_type == "not_equals":
        return {"passed": actual != expected}
    if comparison_type in {"contains", "includes"}:
        return {"passed": str(expected) in str(actual)}
    if comparison_type in {"greater_than", "gt"}:
        return {"passed": (_coerce_float(actual) or 0.0) > (_coerce_float(expected) or 0.0)}
    if comparison_type in {"less_than", "lt"}:
        return {"passed": (_coerce_float(actual) or 0.0) < (_coerce_float(expected) or 0.0)}
    return {"passed": False, "details": f"Unsupported comparison_type: {comparison_type}"}


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _path_option(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value))


def _first_path_match(parts: set[str], values: set[str]) -> str:
    for value in sorted(values):
        if value in parts:
            return value
    return ""


def _default_source_path() -> Path | None:
    source_path = Path("external/enterpriseops-gym/src")
    return source_path if source_path.exists() else None


def _add_source_path(source_path: Path | None) -> None:
    if source_path is None:
        return
    value = str(source_path)
    if value not in sys.path:
        sys.path.insert(0, value)
