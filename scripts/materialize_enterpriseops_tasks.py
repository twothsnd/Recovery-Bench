#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DATASET = "ServiceNow-AI/EnterpriseOps-Gym"
DEFAULT_MODES = ("oracle", "plus_5_tools", "plus_10_tools", "plus_15_tools")
DEFAULT_DOMAINS = ("calendar", "csm", "drive", "email", "hr", "itsm", "teams", "hybrid")
JSON_STRING_FIELDS = {"gym_servers_config", "verifiers", "selected_tools", "restricted_tools", "context", "auth_config"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize EnterpriseOps-Gym HF rows into JSON task configs.")
    parser.add_argument("--input-dir", type=Path, default=Path("external/enterpriseops-gym/archives/hf_dataset"))
    parser.add_argument("--output-dir", type=Path, default=Path("external/enterpriseops-gym/tasks"))
    parser.add_argument("--hf-dataset", default=DEFAULT_DATASET)
    parser.add_argument("--modes", nargs="+", default=list(DEFAULT_MODES))
    parser.add_argument("--domains", nargs="+", default=list(DEFAULT_DOMAINS))
    parser.add_argument("--source", choices=("auto", "parquet", "datasets"), default="auto")
    parser.add_argument("--limit", type=int, default=0, help="Optional per split limit for smoke materialization.")
    parser.add_argument("--overwrite", action="store_true", help="Rewrite existing task JSON files.")
    args = parser.parse_args()

    manifest: dict[str, Any] = {
        "hf_dataset": args.hf_dataset,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": args.source,
        "splits": {},
    }
    total = 0
    for mode in args.modes:
        for domain in args.domains:
            rows, source_path = _load_rows(
                input_dir=args.input_dir,
                hf_dataset=args.hf_dataset,
                mode=mode,
                domain=domain,
                source=args.source,
            )
            if args.limit > 0:
                rows = rows[: args.limit]
            split_dir = args.output_dir / mode / domain
            split_dir.mkdir(parents=True, exist_ok=True)
            written = 0
            for index, row in enumerate(rows):
                config = _normalize_row(row, mode=mode, domain=domain, index=index)
                task_id = str(config["task_id"])
                path = split_dir / f"{_safe_file_name(task_id)}.json"
                if path.exists() and not args.overwrite:
                    continue
                path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                written += 1
            key = f"{mode}/{domain}"
            manifest["splits"][key] = {
                "rows": len(rows),
                "written": written,
                "output_dir": str(split_dir),
                "source_path": str(source_path) if source_path is not None else None,
            }
            total += len(rows)
            print(f"{key}: rows={len(rows)} written={written} output={split_dir}")

    manifest["total_rows"] = total
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"materialized {total} EnterpriseOps-Gym tasks under {args.output_dir}")
    return 0


def _load_rows(
    *,
    input_dir: Path,
    hf_dataset: str,
    mode: str,
    domain: str,
    source: str,
) -> tuple[list[dict[str, Any]], Path | None]:
    errors: list[str] = []
    if source in {"auto", "parquet"}:
        path = _find_parquet(input_dir, mode, domain)
        if path is not None:
            try:
                return _read_parquet(path), path
            except Exception as exc:
                errors.append(f"parquet {path}: {type(exc).__name__}: {exc}")
        elif source == "parquet":
            raise FileNotFoundError(f"No parquet file found for {mode}/{domain} under {input_dir}")

    if source in {"auto", "datasets"}:
        try:
            from datasets import load_dataset
        except Exception as exc:
            errors.append(f"datasets import: {type(exc).__name__}: {exc}")
        else:
            try:
                dataset = load_dataset(hf_dataset, mode, split=domain, trust_remote_code=False)
                return [dict(row) for row in dataset], None
            except Exception as exc:
                errors.append(f"datasets.load_dataset {hf_dataset} {mode}/{domain}: {type(exc).__name__}: {exc}")

    joined = "; ".join(errors) if errors else "no source attempted"
    raise RuntimeError(f"Could not load EnterpriseOps-Gym split {mode}/{domain}: {joined}")


def _find_parquet(input_dir: Path, mode: str, domain: str) -> Path | None:
    candidates = (
        input_dir / mode / f"{domain}-00000-of-00001.parquet",
        input_dir / mode / f"{domain}.parquet",
        input_dir / f"{mode}__{domain}.parquet",
    )
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except Exception:
        try:
            import pandas as pd
        except Exception as exc:
            raise RuntimeError("Reading parquet requires pyarrow or pandas with a parquet engine") from exc
        return [dict(row) for row in pd.read_parquet(path).to_dict(orient="records")]
    table = pq.read_table(path)
    return [dict(row) for row in table.to_pylist()]


def _normalize_row(row: dict[str, Any], *, mode: str, domain: str, index: int) -> dict[str, Any]:
    config: dict[str, Any] = {}
    task_id = str(row.get("task_id") or f"{domain}_{index:04d}")
    for key, value in row.items():
        if value is None:
            continue
        if key in JSON_STRING_FIELDS and isinstance(value, str):
            stripped = value.strip()
            if stripped:
                value = json.loads(stripped)
        config[key] = _jsonable(value)
    config["task_id"] = task_id
    config["domain"] = str(row.get("domain") or domain)
    config["mode"] = mode
    config.setdefault("number_of_runs", 1)
    config.setdefault("verifiers", [])
    config.setdefault("context", {})
    return config


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "as_py"):
        return _jsonable(value.as_py())
    return value


def _safe_file_name(value: str) -> str:
    value = value.strip() or "task"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


if __name__ == "__main__":
    raise SystemExit(main())
