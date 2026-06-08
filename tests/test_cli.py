from __future__ import annotations

import sys

import pytest

from recovery_bench.cli import main


def test_check_benchmark_cli_passes_for_external_adapter(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recovery-bench",
            "check-benchmark",
            "--config",
            "configs/external_minimal_adapter.example.toml",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Benchmark: external-minimal" in output
    assert "Passed: yes" in output
    assert "PASS reset" in output
