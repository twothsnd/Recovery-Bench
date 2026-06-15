#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _env_subset(keys: tuple[str, ...]) -> dict[str, str]:
    return {key: os.environ[key] for key in keys if os.environ.get(key)}


async def _pre_verifier_barrier(ready_path: Path, done_path: Path, timeout_sec: int) -> None:
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(
        json.dumps({"event": "verification_start", "time": time.time()}),
        encoding="utf-8",
    )
    start = time.time()
    while not done_path.exists():
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"timed out waiting for host VM snapshot marker: {done_path}")
        await asyncio.sleep(0.5)


async def _run(args: argparse.Namespace) -> Any:
    from harbor.models.trial.config import (
        AgentConfig as HarborAgentConfig,
        EnvironmentConfig as HarborEnvironmentConfig,
        TaskConfig as HarborTaskConfig,
        TrialConfig,
        VerifierConfig as HarborVerifierConfig,
    )
    from harbor.trial.hooks import TrialEvent
    from harbor.trial.trial import Trial

    kwargs: dict[str, Any] = {
        "parser_name": args.parser_name,
        "temperature": args.temperature,
    }
    if args.api_base:
        kwargs["api_base"] = args.api_base
    if args.memory_path and Path(args.memory_path).is_file() and Path(args.memory_path).stat().st_size > 0:
        kwargs["recovery_memory_path"] = args.memory_path

    agent_env = _env_subset(
        (
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "OPENAI_BASE_URL",
            "NO_PROXY",
            "no_proxy",
        )
    )
    config = TrialConfig(
        task=HarborTaskConfig(path=Path(args.task_path)),
        trial_name=args.trial_name,
        trials_dir=Path(args.attempt_dir) / "trials",
        agent=HarborAgentConfig(
            import_path="tb2_recovery.terminus2_memory_agent:RecoveryTerminus2",
            model_name=args.model_name,
            kwargs=kwargs,
            env=agent_env,
        ),
        environment=HarborEnvironmentConfig(
            delete=False,
            mounts_json=None,
            env={},
        ),
        verifier=HarborVerifierConfig(disable=False),
    )

    async def on_verification_start(_event: Any) -> None:
        await _pre_verifier_barrier(
            Path(args.snapshot_ready_path),
            Path(args.snapshot_done_path),
            args.snapshot_timeout_sec,
        )

    trial = await Trial.create(config)
    trial.add_hook(TrialEvent.VERIFICATION_START, on_verification_start)
    return await trial.run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one TB2 Terminus2 attempt inside a QEMU guest.")
    parser.add_argument("--task-path", required=True)
    parser.add_argument("--attempt-dir", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--memory-path", default="")
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--api-base", default="")
    parser.add_argument("--parser-name", default="json")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--trial-name", required=True)
    parser.add_argument("--snapshot-ready-path", required=True)
    parser.add_argument("--snapshot-done-path", required=True)
    parser.add_argument("--snapshot-timeout-sec", type=int, default=900)
    args = parser.parse_args()

    result_path = Path(args.result_path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(_run(args))
        data = _jsonable(result)
        result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"result_path": str(result_path), "success": True}, ensure_ascii=False))
        return 0
    except BaseException as exc:
        data = {
            "exception_info": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "success": False,
        }
        result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"result_path": str(result_path), "success": False, "error": data["exception_info"]}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
