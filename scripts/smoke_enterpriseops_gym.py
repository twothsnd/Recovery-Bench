#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recovery_bench.adapters.enterpriseops_gym import EnterpriseOpsGymBenchmarkAdapter


DEFAULT_DOMAINS = ("calendar", "csm", "drive", "email", "hr", "itsm", "teams", "hybrid")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test local EnterpriseOps-Gym task configs and MCP servers.")
    parser.add_argument("--source-path", type=Path, default=Path("external/enterpriseops-gym/src"))
    parser.add_argument("--tasks-root", type=Path, default=Path("external/enterpriseops-gym/tasks"))
    parser.add_argument("--mode", default="oracle")
    parser.add_argument("--domain", action="append", default=None)
    parser.add_argument("--task-index", type=int, default=0)
    parser.add_argument(
        "--mcp-server-url-overrides",
        nargs="*",
        default=(),
        metavar="KEY=URL",
        help="Runtime MCP URL overrides keyed by domain or MCP server name.",
    )
    args = parser.parse_args()

    overrides = _parse_overrides(args.mcp_server_url_overrides)
    domains = tuple(args.domain) if args.domain else DEFAULT_DOMAINS
    failures: list[tuple[str, str]] = []

    for domain in domains:
        adapter: EnterpriseOpsGymBenchmarkAdapter | None = None
        try:
            adapter = EnterpriseOpsGymBenchmarkAdapter(
                source_path=args.source_path,
                configs_folder=args.tasks_root / args.mode / domain,
                domain=domain,
                mode=args.mode,
                options={"mcp_server_url_overrides": overrides},
            )
            ids = adapter.list_tasks()
            if not ids:
                raise RuntimeError(f"no tasks found for {args.mode}/{domain}")
            task = adapter.load_task(ids[args.task_index])
            snapshot = adapter.reset(task)
            env = adapter.agent_environment()
            outcome = adapter.evaluate(task)
            summary = outcome.details.get("verification_summary", {})
            print(
                f"{domain}: tasks={len(ids)} task={task.task_id} "
                f"gyms={len(snapshot.payload['gyms'])} tools={len(env.available_tools)} "
                f"pre_success={outcome.success} score={outcome.score:.3f} verifiers={summary}"
            )
        except Exception as exc:
            failures.append((domain, f"{type(exc).__name__}: {exc}"))
            print(f"{domain}: FAIL {type(exc).__name__}: {exc}")
        finally:
            if adapter is not None:
                adapter.close()

    if failures:
        print(f"failures={failures}")
        return 1
    return 0


def _parse_overrides(items: tuple[str, ...] | list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"invalid override {item!r}; expected KEY=URL")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise SystemExit(f"invalid override {item!r}; expected KEY=URL")
        overrides[key] = value
    return overrides


if __name__ == "__main__":
    raise SystemExit(main())
