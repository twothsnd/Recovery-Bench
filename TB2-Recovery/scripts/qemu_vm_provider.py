#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GUEST_RUNNER = ROOT / "scripts" / "qemu_guest_harbor_attempt.py"
TERMINUS2_AGENT = ROOT / "tb2_recovery" / "terminus2_memory_agent.py"
INIT_FILE = ROOT / "tb2_recovery" / "__init__.py"


def _env(name: str, default: str = "", *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"{name} is required")
    return value


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "unnamed"


def _run(argv: list[str], *, input_bytes: bytes | None = None, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(argv, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(shlex.quote, argv))}\n"
            f"stdout:\n{proc.stdout.decode(errors='replace')}\n"
            f"stderr:\n{proc.stderr.decode(errors='replace')}"
        )
    return proc


def _which(name: str) -> str | None:
    path = shutil.which(name)
    return path if path else None


class QEMUProvider:
    def __init__(self, session_id: str):
        self.session_id = _safe(session_id)
        self.home = Path(_env("TB2_QEMU_HOME", str(ROOT / "vm" / "qemu"))).expanduser()
        self.session_dir = self.home / "sessions" / self.session_id
        self.overlay = self.session_dir / "disk.qcow2"
        self.qmp_sock = self.session_dir / "qmp.sock"
        self.pid_file = self.session_dir / "qemu.pid"
        self.serial_log = self.session_dir / "serial.log"
        self.ssh_port_file = self.session_dir / "ssh_port"
        self.ssh_user = _env("TB2_QEMU_SSH_USER", "ubuntu")
        self.ssh_key = _env("TB2_QEMU_SSH_KEY", "")
        self.remote_root = _env("TB2_QEMU_REMOTE_ROOT", "/opt/recovery-bench/tb2")
        self.boot_timeout_sec = int(_env("TB2_QEMU_BOOT_TIMEOUT_SEC", "600"))
        self.snapshot_timeout_sec = int(_env("TB2_QEMU_SNAPSHOT_TIMEOUT_SEC", "900"))

    @property
    def ssh_port(self) -> int:
        if self.ssh_port_file.exists():
            return int(self.ssh_port_file.read_text(encoding="utf-8").strip())
        base = int(_env("TB2_QEMU_SSH_PORT_BASE", "22000"))
        port = base + (abs(hash(self.session_id)) % 1000)
        self.ssh_port_file.write_text(str(port), encoding="utf-8")
        return port

    def _ssh_base(self) -> list[str]:
        argv = [
            "ssh",
            "-p",
            str(self.ssh_port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ]
        if self.ssh_key:
            argv.extend(["-i", self.ssh_key])
        return argv

    def _scp_base(self) -> list[str]:
        argv = [
            "scp",
            "-P",
            str(self.ssh_port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "BatchMode=yes",
        ]
        if self.ssh_key:
            argv.extend(["-i", self.ssh_key])
        return argv

    def ssh(self, command: str, *, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
        return _run(self._ssh_base() + [f"{self.ssh_user}@127.0.0.1", command], check=check, timeout=timeout)

    def remote_sh(self, command: str, *, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
        return self.ssh("bash -lc " + shlex.quote(command), check=check, timeout=timeout)

    def scp_to_guest(self, local: Path, remote: str) -> None:
        self.remote_sh(f"mkdir -p {shlex.quote(str(Path(remote).parent))}")
        _run(self._scp_base() + [str(local), f"{self.ssh_user}@127.0.0.1:{remote}"])

    def copy_dir_to_guest(self, local_dir: Path, remote_parent: str) -> str:
        remote_parent_path = Path(remote_parent)
        remote_path = remote_parent_path / local_dir.name
        self.remote_sh(f"mkdir -p {shlex.quote(str(remote_parent_path))} && rm -rf {shlex.quote(str(remote_path))}")
        tar_proc = subprocess.run(
            ["tar", "-C", str(local_dir.parent), "-czf", "-", local_dir.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        _run(self._ssh_base() + [f"{self.ssh_user}@127.0.0.1", f"tar -C {shlex.quote(str(remote_parent_path))} -xzf -"], input_bytes=tar_proc.stdout)
        return str(remote_path)

    def qmp(self, execute: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        deadline = time.time() + 30
        while not self.qmp_sock.exists():
            if time.time() > deadline:
                raise TimeoutError(f"QMP socket not found: {self.qmp_sock}")
            time.sleep(0.2)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(30)
            sock.connect(str(self.qmp_sock))
            self._qmp_read(sock)
            self._qmp_send(sock, {"execute": "qmp_capabilities"})
            self._qmp_read(sock)
            self._qmp_send(sock, {"execute": execute, "arguments": arguments or {}})
            return self._qmp_read(sock)

    @staticmethod
    def _qmp_send(sock: socket.socket, payload: dict[str, Any]) -> None:
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\r\n")

    @staticmethod
    def _qmp_read(sock: socket.socket) -> dict[str, Any]:
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\r\n" in chunk:
                break
        line = b"".join(chunks).splitlines()[0]
        data = json.loads(line.decode("utf-8"))
        if "error" in data:
            raise RuntimeError(json.dumps(data["error"], ensure_ascii=False))
        return data

    def hmp(self, command_line: str) -> dict[str, Any]:
        return self.qmp("human-monitor-command", {"command-line": command_line})

    def reset(self, *, task_id: str, task_path: str, image: str) -> None:
        del task_id, task_path, image
        base_image = Path(_env("TB2_QEMU_BASE_IMAGE", required=True)).expanduser()
        qemu_img = _env("TB2_QEMU_IMG", _which("qemu-img") or "qemu-img")
        qemu_system = _env("TB2_QEMU_SYSTEM", _which("qemu-system-x86_64") or "qemu-system-x86_64")
        if not base_image.exists():
            raise SystemExit(f"TB2_QEMU_BASE_IMAGE does not exist: {base_image}")
        if not shutil.which(qemu_img) and not Path(qemu_img).exists():
            raise SystemExit(f"qemu-img not found: {qemu_img}")
        if not shutil.which(qemu_system) and not Path(qemu_system).exists():
            raise SystemExit(f"qemu-system-x86_64 not found: {qemu_system}")

        self.clean(remove_disk=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        _run([qemu_img, "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_image), str(self.overlay)])

        memory_mb = _env("TB2_QEMU_MEMORY_MB", "8192")
        cpus = _env("TB2_QEMU_CPUS", "4")
        accel = _env("TB2_QEMU_ACCEL", "auto")
        accel_args = ["-accel", "tcg", "-cpu", "max"]
        if accel == "kvm" or (accel == "auto" and os.access("/dev/kvm", os.R_OK | os.W_OK)):
            accel_args = ["-enable-kvm", "-cpu", "host"]

        cmd = [
            qemu_system,
            "-name",
            f"tb2-{self.session_id}",
            "-m",
            str(memory_mb),
            "-smp",
            str(cpus),
            *accel_args,
            "-drive",
            f"file={self.overlay},if=virtio,format=qcow2",
            "-netdev",
            f"user,id=n0,hostfwd=tcp:127.0.0.1:{self.ssh_port}-:22",
            "-device",
            "virtio-net-pci,netdev=n0",
            "-qmp",
            f"unix:{self.qmp_sock},server=on,wait=off",
            "-daemonize",
            "-pidfile",
            str(self.pid_file),
            "-serial",
            f"file:{self.serial_log}",
            "-display",
            "none",
        ]
        extra = _env("TB2_QEMU_EXTRA_ARGS", "")
        if extra:
            cmd.extend(shlex.split(extra))
        _run(cmd)
        self.wait_ssh()

    def wait_ssh(self) -> None:
        deadline = time.time() + self.boot_timeout_sec
        last = ""
        while time.time() < deadline:
            proc = self.remote_sh("true", check=False)
            if proc.returncode == 0:
                return
            last = proc.stderr.decode(errors="replace")
            time.sleep(3)
        raise TimeoutError(f"guest SSH did not become ready on port {self.ssh_port}: {last}")

    def attempt(self, args: argparse.Namespace) -> dict[str, Any]:
        self.wait_ssh()
        remote_attempt_dir = f"{self.remote_root}/sessions/{self.session_id}/{args.task_id}/{args.protocol}/attempt_{args.attempt_index}"
        remote_task_parent = f"{remote_attempt_dir}/task"
        remote_task_path = self.copy_dir_to_guest(Path(args.task_path), remote_task_parent)
        remote_memory_path = f"{remote_attempt_dir}/memory/recovery_memory.md"
        self.remote_sh(f"mkdir -p {shlex.quote(str(Path(remote_memory_path).parent))}")
        if args.memory_path and args.memory_path != "/dev/null" and Path(args.memory_path).is_file():
            self.scp_to_guest(Path(args.memory_path), remote_memory_path)
        else:
            self.remote_sh(f": > {shlex.quote(remote_memory_path)}")

        self.remote_sh(f"mkdir -p {shlex.quote(self.remote_root)}/tb2_recovery")
        self.scp_to_guest(GUEST_RUNNER, f"{self.remote_root}/qemu_guest_harbor_attempt.py")
        self.scp_to_guest(TERMINUS2_AGENT, f"{self.remote_root}/tb2_recovery/terminus2_memory_agent.py")
        self.scp_to_guest(INIT_FILE, f"{self.remote_root}/tb2_recovery/__init__.py")

        snapshot_id = _safe(f"{self.session_id}-{args.task_id}-{args.protocol}-a{args.attempt_index}-{int(time.time())}")
        ready_path = f"{remote_attempt_dir}/snapshot_ready.json"
        done_path = f"{remote_attempt_dir}/snapshot_done"
        remote_result_path = f"{remote_attempt_dir}/result.json"
        trial_name = _safe(f"rb-{args.task_id}-{args.protocol}-a{args.attempt_index}")

        env = {
            "PYTHONPATH": f"{self.remote_root}:$PYTHONPATH",
            "OPENAI_API_KEY": _env("OPENAI_API_KEY", _env("TB2_OPENAI_API_KEY", "EMPTY")),
            "OPENAI_API_BASE": args.api_base,
            "OPENAI_BASE_URL": args.api_base,
            "NO_PROXY": _env("NO_PROXY", "127.0.0.1,localhost,172.17.0.1,host.docker.internal"),
            "no_proxy": _env("no_proxy", _env("NO_PROXY", "127.0.0.1,localhost,172.17.0.1,host.docker.internal")),
            "PIP_INDEX_URL": _env("PIP_INDEX_URL", ""),
            "PIP_TRUSTED_HOST": _env("PIP_TRUSTED_HOST", ""),
            "UV_INDEX_URL": _env("UV_INDEX_URL", ""),
            "GH_PROXY": _env("GH_PROXY", ""),
            "TB2_WHEELHOUSE": _env("TB2_QEMU_GUEST_WHEELHOUSE", _env("TB2_WHEELHOUSE", "")),
            "TB2_WHEELHOUSE_IN_CONTAINER": _env("TB2_WHEELHOUSE_IN_CONTAINER", "/opt/tb2/wheelhouse"),
            "TB2_LOCAL_PKG_DELAY_SEC": _env("TB2_LOCAL_PKG_DELAY_SEC", "5"),
        }
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items() if value)
        guest_python = _env("TB2_QEMU_GUEST_PYTHON", "python3")
        guest_cmd = (
            f"cd {shlex.quote(self.remote_root)} && {exports} {shlex.quote(guest_python)} qemu_guest_harbor_attempt.py "
            f"--task-path {shlex.quote(remote_task_path)} "
            f"--attempt-dir {shlex.quote(remote_attempt_dir)} "
            f"--result-path {shlex.quote(remote_result_path)} "
            f"--memory-path {shlex.quote(remote_memory_path)} "
            f"--model-name {shlex.quote(args.model_name)} "
            f"--api-base {shlex.quote(args.api_base)} "
            f"--parser-name {shlex.quote(args.parser_name)} "
            f"--temperature {shlex.quote(str(args.temperature))} "
            f"--trial-name {shlex.quote(trial_name)} "
            f"--snapshot-ready-path {shlex.quote(ready_path)} "
            f"--snapshot-done-path {shlex.quote(done_path)} "
            f"--snapshot-timeout-sec {self.snapshot_timeout_sec}"
        )
        proc = subprocess.Popen(
            self._ssh_base() + [f"{self.ssh_user}@127.0.0.1", "bash -lc " + shlex.quote(guest_cmd)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            self._wait_remote_file(ready_path, proc)
            self.hmp(f"savevm {snapshot_id}")
            self.remote_sh(f"touch {shlex.quote(done_path)}")
            stdout, stderr = proc.communicate(timeout=None)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"guest attempt failed ({proc.returncode})\n"
                    f"stdout:\n{stdout.decode(errors='replace')}\n"
                    f"stderr:\n{stderr.decode(errors='replace')}"
                )
            result_proc = self.remote_sh(f"cat {shlex.quote(remote_result_path)}")
            result = json.loads(result_proc.stdout.decode("utf-8"))
        finally:
            if proc.poll() is None:
                proc.kill()

        result["snapshot_id"] = snapshot_id
        result["pre_score_snapshot_id"] = snapshot_id
        result.setdefault("trial_name", trial_name)
        result.setdefault("trial_dir", f"{remote_attempt_dir}/trials/{trial_name}")
        result.setdefault("result_path", remote_result_path)
        result.setdefault("qemu_session_id", self.session_id)
        Path(args.result_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, default=str))
        return result

    def _wait_remote_file(self, remote_path: str, proc: subprocess.Popen[bytes]) -> None:
        deadline = time.time() + self.snapshot_timeout_sec
        while time.time() < deadline:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise RuntimeError(
                    f"guest attempt exited before VERIFICATION_START ({proc.returncode})\n"
                    f"stdout:\n{stdout.decode(errors='replace')}\n"
                    f"stderr:\n{stderr.decode(errors='replace')}"
                )
            test = self.remote_sh(f"test -s {shlex.quote(remote_path)}", check=False)
            if test.returncode == 0:
                return
            time.sleep(1)
        raise TimeoutError(f"timed out waiting for guest pre-verifier marker: {remote_path}")

    def restore(self, snapshot_id: str) -> None:
        self.hmp("stop")
        self.hmp(f"loadvm {snapshot_id}")
        self.hmp("cont")
        self.wait_ssh()

    def discard(self, snapshot_id: str) -> None:
        try:
            self.hmp(f"delvm {snapshot_id}")
        except Exception:
            pass

    def clean(self, *, remove_disk: bool = False) -> None:
        if self.pid_file.exists():
            pid = self.pid_file.read_text(encoding="utf-8").strip()
            if pid:
                subprocess.run(["kill", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
        if remove_disk:
            shutil.rmtree(self.session_dir, ignore_errors=True)

    def doctor(self) -> dict[str, Any]:
        data = {
            "qemu_system": _env("TB2_QEMU_SYSTEM", _which("qemu-system-x86_64") or ""),
            "qemu_img": _env("TB2_QEMU_IMG", _which("qemu-img") or ""),
            "base_image": _env("TB2_QEMU_BASE_IMAGE", ""),
            "dev_kvm_exists": Path("/dev/kvm").exists(),
            "dev_kvm_access": os.access("/dev/kvm", os.R_OK | os.W_OK),
            "ssh": _which("ssh"),
            "scp": _which("scp"),
            "tar": _which("tar"),
            "session_dir": str(self.session_dir),
        }
        print(json.dumps(data, indent=2))
        return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict QEMU VM provider for TB2-Recovery.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    reset = sub.add_parser("reset")
    reset.add_argument("--task-id", required=True)
    reset.add_argument("--task-path", required=True)
    reset.add_argument("--image", required=True)
    reset.add_argument("--session-id", required=True)

    attempt = sub.add_parser("attempt")
    attempt.add_argument("--task-id", required=True)
    attempt.add_argument("--task-path", required=True)
    attempt.add_argument("--attempt-dir", required=True)
    attempt.add_argument("--result-path", required=True)
    attempt.add_argument("--memory-path", default="/dev/null")
    attempt.add_argument("--model-name", required=True)
    attempt.add_argument("--api-base", default="")
    attempt.add_argument("--attempt-index", required=True)
    attempt.add_argument("--protocol", required=True)
    attempt.add_argument("--session-id", required=True)
    attempt.add_argument("--parser-name", default="json")
    attempt.add_argument("--temperature", default="0.7")

    restore = sub.add_parser("restore")
    restore.add_argument("--snapshot-id", required=True)
    restore.add_argument("--session-id", required=True)

    discard = sub.add_parser("discard")
    discard.add_argument("--snapshot-id", required=True)
    discard.add_argument("--session-id", required=True)

    clean = sub.add_parser("clean")
    clean.add_argument("--session-id", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--session-id", default="doctor")
    return parser


def main() -> int:
    args = _parser().parse_args()
    provider = QEMUProvider(args.session_id)
    if args.cmd == "reset":
        provider.reset(task_id=args.task_id, task_path=args.task_path, image=args.image)
    elif args.cmd == "attempt":
        provider.attempt(args)
    elif args.cmd == "restore":
        provider.restore(args.snapshot_id)
    elif args.cmd == "discard":
        provider.discard(args.snapshot_id)
    elif args.cmd == "clean":
        provider.clean(remove_disk=True)
    elif args.cmd == "doctor":
        provider.doctor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
