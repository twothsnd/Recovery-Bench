# TB2-Recovery

Local Recovery-Bench adapter project for Terminal-Bench 2 with the Terminus2
agent. TB2-Recovery uses Terminus2 for model-driven attempts; different
external models are connected through Terminus2's OpenAI-compatible or
LiteLLM-compatible configuration.

This project is intentionally self-contained. Recovery-Bench core schedules the
Success@1, Retry@k, and Recovery@k protocols; this folder owns the TB2-specific
task-image preparation, Docker state backend, and Terminus2 configuration.

## Design

- The default state backend is `docker_commit`.
- Each attempt runs through Harbor's official Trial API with Terminus2.
- At Harbor `VERIFICATION_START`, the adapter commits the main task container
  before the official verifier runs.
- Retry starts from the original task image and a fresh Harbor/Terminus2 trial.
- Recovery patches the next attempt's task image to the previous failed
  attempt's committed pre-verifier image, then injects previous failed
  trajectories as Terminus2 memory.
- Local preparation covers task image prebaking and Docker image pull policy.
  Agent-attempt package installation and network behavior stay under the
  official TB2/Terminus2 task environment.

## Requirements

Use the repository Python environment to run Recovery-Bench core.

```bash
PYTHONPATH=/data/xiewei/Recovery-Bench/src:/data/xiewei/Recovery-Bench/TB2-Recovery \
/data/xiewei/Recovery-Bench/.venv/bin/python -m recovery_bench.cli suite \
  --config /data/xiewei/Recovery-Bench/TB2-Recovery/configs/tb2_terminus2.local.example.toml
```

Start an OpenAI-compatible server before running Terminus2. The configured
`api_base` must be reachable from Harbor's Terminus2 agent process.

## Config

The example config points at:

```text
/data/xiewei/Recovery-Bench/external/terminal-bench-2
```

Edit `configs/tb2_terminus2.local.example.toml` for the model name, API base,
task list, and run directory.

## Local Preparation

The adapter can prepare task images before the first clean attempt. This is
configured under `benchmark.options.local_optimization`.

- `prebake_images` runs `scripts/prebake_task_image.sh` before the first clean
  attempt for a task.
- `mutate_original_images=true` commits prebake changes back into the active
  image tag, matching the earlier local TB2 runner.
- `docker_mirror_prefix`, `docker_pull_retries`, and
  `docker_pull_total_timeout_sec` follow the earlier local pull policy.

Prebake installs only the tools needed for Harbor/Terminus2 execution, currently
`tmux` and optionally `asciinema` when missing. Package managers and runtime
download commands remain the task image's own tools.

## Docker Backend

```toml
[benchmark.options]
state_backend = "docker_commit"
```

This backend is a practical substitute for strict VM recovery. It preserves the
main container filesystem at the same protocol boundary, before official
verification. It does not preserve process memory, live sockets, sidecar
services, anonymous runtime state, or Docker volumes, so it reports
`strict_recovery=false` in benchmark capabilities.

The QEMU provider scripts remain in `scripts/` for a future strict VM backend,
but they are not used by the default config.

## Notes

The Docker backend is enough to test the TB2 workflow and model plumbing. Treat
reported Recovery@k as filesystem-state recovery, not strict VM recovery.
