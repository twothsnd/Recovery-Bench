# Recovery Bench

Recovery Bench is a framework for evaluating `Recovery@k`: stateful agent retries where each failed attempt leaves the environment dirty and the next attempt must repair-and-continue from that inherited state.

The research plan is in [recovery_at_k_plan.md](recovery_at_k_plan.md).

## Core Protocols

- `Success@1`: one clean attempt.
- `Retry@k`: failed attempts reset back to clean state before the next attempt.
- `Recovery@k`: failed attempts do not reset; the next attempt inherits the previous state.

The standard suite reports:

```text
Success@1
Retry@2
Recovery@2
Retry@3
Recovery@3
Recovery Gap = Retry@k - Recovery@k
```

## Smoke Check

The built-in smoke benchmark verifies the key semantic distinction: `Recovery@2` succeeds by preserving progress from attempt 1, while `Retry@2` fails because reset discards that progress.

```bash
PYTHONPATH=src python3 -m recovery_bench.cli suite --config configs/progress_smoke.toml
```

Outputs:

- `runs/progress-smoke/main.md`: main comparison table.
- `runs/progress-smoke/summary.md`: protocol/k aggregate table.
- `runs/progress-smoke/main.csv` and `summary.csv`: machine-readable tables.
- `runs/progress-smoke/manifest.json`: run metadata and per-task outcomes.
- `runs/progress-smoke/artifacts/`: per-task JSON artifacts.

You can still override config values from the CLI:

```bash
PYTHONPATH=src python3 -m recovery_bench.cli suite \
  --config configs/progress_smoke.toml \
  --output-dir runs/progress-smoke-override \
  --k 1 --k 2 --k 3
```

## External Benchmarks

Do not use `git pull` for benchmark source fetching in this project. Prefer downloading versioned archive files with `wget`, then unpacking them under `external/`.

```bash
scripts/download_benchmark_archive.sh NAME URL
```

For the first-wave source checkouts:

```bash
scripts/download_first_benchmarks.sh
```

The downloader uses official sources only. For GitHub archive URLs it first
tries the normal `github.com/.../archive/...` URL, then the official
`codeload.github.com` URL. If local DNS routes GitHub to a TLS-resetting
endpoint, it retries official codeload IPs with `Host: codeload.github.com`.
Override that last fallback with:

```bash
GITHUB_CODELOAD_IPS="140.82.112.10 140.82.113.10" scripts/download_first_benchmarks.sh
```

After the official source archives are downloaded, install runtime dependencies
from the local source checkouts:

```bash
scripts/install_benchmark_deps.sh
```

Useful overrides:

```bash
PYTHON=python3.13 INSTALL_APPWORLD=0 scripts/install_benchmark_deps.sh
PIP_EXTRA_ARGS="--upgrade" scripts/install_benchmark_deps.sh
```

AppWorld also needs its official data bundle:

```bash
PYTHON=.venv/bin/python scripts/download_appworld_data.sh
```

If a single S3 connection is slow, use parallel byte-range downloads:

```bash
APPWORLD_DOWNLOAD_MODE=parallel APPWORLD_DOWNLOAD_JOBS=16 PYTHON=.venv/bin/python scripts/download_appworld_data.sh
```

If DNS picks a slow S3 endpoint, pin an official S3 IP while preserving the
official host header:

```bash
APPWORLD_S3_IP=52.92.180.57 PYTHON=.venv/bin/python scripts/download_appworld_data.sh
```

If this environment throttles `wget` on S3, the same script can still use the
official S3 URL with resumable `curl`:

```bash
APPWORLD_DOWNLOADER=curl APPWORLD_S3_IP=52.92.186.193 PYTHON=.venv/bin/python scripts/download_appworld_data.sh
```

AppWorld's GitHub archive contains Git LFS pointers for encrypted source
bundles. Keep `external/appworld/src` as the untouched official archive
extraction and materialize a separate runtime copy:

```bash
scripts/download_appworld_source_bundles.sh
```

EnterpriseOps-Gym source includes `gym_dbs.zip`, and task configs are loaded
from the official Hugging Face dataset `ServiceNow-AI/EnterpriseOps-Gym` unless
`benchmark.options.configs_folder` points to a local materialized config folder.
Runtime execution also requires the official MCP Docker servers for the chosen
domain to be running.

Recommended setup keeps the official source checkout untouched and materializes
task configs into our own cache:

```bash
# Needed for local parquet -> JSON materialization.
.venv/bin/python -m pip install -e '.[enterpriseops-gym]'

# wget/curl/hf paths all use official Hugging Face URLs.
ENTERPRISEOPS_DOWNLOADER=wget scripts/download_enterpriseops_tasks.sh

# If direct wget/curl to huggingface.co is blocked, either use the official
# datasets API path or explicitly opt in to a Hugging Face mirror transport.
ENTERPRISEOPS_DOWNLOADER=datasets scripts/download_enterpriseops_tasks.sh
ENTERPRISEOPS_HF_BASE=https://hf-mirror.com/datasets \
  ENTERPRISEOPS_ALLOW_MIRROR=1 \
  ENTERPRISEOPS_DOWNLOADER=wget \
  scripts/download_enterpriseops_tasks.sh

# Start official MCP Docker servers for the selected domain(s).
ENTERPRISEOPS_DOMAINS="teams" scripts/start_enterpriseops_gym_servers.sh

# If Docker Hub direct pull is blocked, use a Docker Hub mirror transport.
ENTERPRISEOPS_DOCKER_MIRROR_PREFIXES="docker.1ms.run hub.rat.dev" \
  ENTERPRISEOPS_DOMAINS="teams" \
  scripts/start_enterpriseops_gym_servers.sh

# Override occupied host ports without changing official task configs.
ENTERPRISEOPS_CSM_HOST_PORT=8011 \
  ENTERPRISEOPS_DOMAINS="csm" \
  scripts/start_enterpriseops_gym_servers.sh

PYTHONPATH=src .venv/bin/python scripts/smoke_enterpriseops_gym.py \
  --mcp-server-url-overrides csm=http://localhost:8011 sn-csm-server=http://localhost:8011
```

ClawsBench source is downloaded from the official repository, but the current
official repo only contains the website and trajectory placeholders. Its README
states that tasks will be added later, so the benchmark remains registered as
`planned` until executable task/environment assets are released.

Adapter implementation notes are in [docs/adapter_guide.md](docs/adapter_guide.md).
New benchmark and agent integrations should normally be loaded through
`benchmark.import_path` and `agent.import_path`, so group members can keep their
adapter code outside the core registry.

The minimal external adapter example is:

- `examples/adapters/minimal_recovery_adapter.py`
- `configs/external_minimal_adapter.example.toml`

Run its conformance check:

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli check-benchmark \
  --config configs/external_minimal_adapter.example.toml
```

Run its full retry/recovery suite:

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli suite \
  --config configs/external_minimal_adapter.example.toml
```

Example configs for the first real targets are in:

- `configs/appworld.example.toml`
- `configs/tau_bench.example.toml`
- `configs/clawsbench.example.toml`
- `configs/enterpriseops_gym.example.toml`

Codex-backed OpenAI-compatible configs for the runnable first-wave targets are:

- `configs/appworld.codex_gpt55.toml`
- `configs/tau_bench.codex_gpt55.toml`
- `configs/enterpriseops_gym.codex_gpt55.toml`

Claude Sonnet configs using `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` are:

- `configs/appworld.claude_sonnet.toml`
- `configs/tau_bench.claude_sonnet.toml`
- `configs/enterpriseops_gym.claude_sonnet.toml`

Claude Opus configs use the same Anthropic-compatible endpoint:

- `configs/appworld.claude_opus.toml`
- `configs/tau_bench.claude_opus.toml`
- `configs/enterpriseops_gym.claude_opus.toml`

The first benchmark wave is:

- AppWorld
- τ-bench
- ClawsBench
- EnterpriseOps-Gym

## Agent Backends

Provider-backed agents are configured through `[agent]` and `[model]`.

Registered provider agents:

- `openai-agent`: uses the OpenAI Responses API.
- `anthropic-agent`: uses the Anthropic Messages API.
- `gemini-agent`: uses the Google GenAI client.

Provider SDKs and API keys are checked lazily. `list-agents` reports whether each provider is currently runnable without making smoke tests depend on those packages:

```bash
PYTHONPATH=src python3 -m recovery_bench.cli list-agents
```

`openai-agent` first uses standard `OPENAI_API_KEY` / `OPENAI_BASE_URL`.
If those are not set, it can read Codex's local OpenAI-compatible settings from
`~/.codex/auth.json` and `~/.codex/config.toml` without printing or storing the
secret in run artifacts. The bundled `*.codex_gpt55.toml` configs use that path.

`anthropic-agent` uses the Anthropic SDK when installed. In this environment it
can also call the Anthropic Messages API directly with `httpx` using
`ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, plus optional
`ANTHROPIC_BASE_URL`.

The generic provider agent does not guess benchmark action formats. It calls a benchmark-native bridge exposed by `benchmark.agent_environment()`, preferably `run_recovery_bench_agent(...)`. Implemented benchmark bridges:

- AppWorld: model response -> Python code block -> `world.execute(code)`.
- τ-bench: model response -> action string -> `env.step(action)`.
- EnterpriseOps-Gym: model response -> JSON MCP tool call -> official MCP server.

Use `[agent.options]` for execution controls such as `max_steps`.

τ-bench's official Gym flow also uses an LLM-backed user simulator. Set
`benchmark.options.user_llm` to a LiteLLM model name; the adapter fills
`user_llm_args` credentials from the local OpenAI/Codex or Anthropic env without
writing secrets to artifacts. The closed-model configs use
`anthropic/claude-sonnet-4-5` for this user simulator because the local OpenAI
Chat Completions route is not available for the Codex `gpt-5.5` provider.
