"""SSH Executor and Remote Workspace Backend.

Provides `SSHExecutor` (implementing `Executor`) for executing shell commands and background
tasks over SSH connections to remote VPS / Linux servers, and `RemoteWorkspaceBackend` for
SFTP file operations and remote searches.
"""

from __future__ import annotations

import io
import os
import posixpath
import queue
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import paramiko

from .shell import _DEFAULT_TIMEOUT, _MAX_TIMEOUT, _NONINTERACTIVE_ENV, Executor


@dataclass
class SSHConnectionConfig:
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    key_path: Optional[str] = None
    key_passphrase: Optional[str] = None
    remote_cwd: str = "/root"
    connect_timeout: float = 15.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SSHConnectionConfig:
        return cls(
            host=str(data.get("host", "")).strip(),
            port=int(data.get("port", 22) or 22),
            username=str(data.get("username", "root")).strip() or "root",
            password=data.get("password") or None,
            key_path=data.get("key_path") or None,
            key_passphrase=data.get("key_passphrase") or None,
            remote_cwd=str(data.get("remote_cwd", "/root")).strip() or "/root",
            connect_timeout=float(data.get("connect_timeout", 15.0) or 15.0),
        )


class _SSHBackgroundTask:
    """Detached background task running remotely via SSH."""

    def __init__(
        self,
        task_id: str,
        command: str,
        client: paramiko.SSHClient,
        remote_cwd: str,
    ):
        self.id = task_id
        self.command = command
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._cursor = 0
        self._is_alive = True
        self._channel: Optional[paramiko.Channel] = None
        self._client = client
        self._remote_cwd = remote_cwd

        # Start thread to run remote process
        self._worker = threading.Thread(target=self._run_remote, daemon=True)
        self._worker.start()

    def _run_remote(self) -> None:
        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                with self._lock:
                    self._lines.append("[SSH error: Transport inactive]\n")
                    self._is_alive = False
                return

            self._channel = transport.open_session()
            self._channel.set_combine_stderr(True)
            escaped_cwd = shlex.quote(self._remote_cwd)
            wrapped_cmd = f"cd {escaped_cwd} && {self.command}"
            self._channel.exec_command(wrapped_cmd)

            makefile = self._channel.makefile("r", bufsize=1)
            for line in makefile:
                with self._lock:
                    self._lines.append(line)
            self._channel.close()
        except Exception as e:
            with self._lock:
                self._lines.append(f"[SSH execution error: {e}]\n")
        finally:
            with self._lock:
                self._is_alive = False

    def read_new(self) -> str:
        with self._lock:
            new = "".join(self._lines[self._cursor :])
            self._cursor = len(self._lines)
        return new

    def kill(self) -> None:
        if self._channel is not None and not self._channel.closed:
            try:
                self._channel.close()
            except Exception:
                pass
        with self._lock:
            self._is_alive = False


class SSHExecutor(Executor):
    """Executes commands on a remote VPS over an SSH persistent session."""

    def __init__(
        self,
        config: SSHConnectionConfig,
        *,
        default_timeout: float = _DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
    ) -> None:
        self.config = config
        self.cwd = config.remote_cwd
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self._marker = f"__COWORKER_SSH_DONE_{uuid.uuid4().hex}__"
        self._bg_tasks: dict[str, _SSHBackgroundTask] = {}
        self._bg_counter = 0
        self._abort = threading.Event()
        self._client: Optional[paramiko.SSHClient] = None
        self._shell_channel: Optional[paramiko.Channel] = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._connect()

    def _connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        key_filename = None
        if self.config.key_path:
            kp = Path(self.config.key_path).expanduser().resolve()
            if kp.is_file():
                key_filename = str(kp)

        client.connect(
            hostname=self.config.host,
            port=self.config.port,
            username=self.config.username,
            password=self.config.password,
            key_filename=key_filename,
            passphrase=self.config.key_passphrase,
            timeout=self.config.connect_timeout,
            banner_timeout=15.0,
            auth_timeout=15.0,
        )
        self._client = client

        # Open persistent interactive shell
        channel = client.invoke_shell(term="vt100", width=120, height=40)
        channel.set_combine_stderr(True)
        self._shell_channel = channel

        # Start drain reader
        self._reader_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._reader_thread.start()

        # Initialize environment and initial working directory
        env_exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in _NONINTERACTIVE_ENV.items())
        init_cmd = f"export {env_exports} && cd {shlex.quote(self.cwd)}"
        self.run(init_cmd, timeout=10.0)

    def _drain_loop(self) -> None:
        assert self._shell_channel is not None
        buf = ""
        while True:
            try:
                if self._shell_channel.closed:
                    break
                if self._shell_channel.recv_ready():
                    data = self._shell_channel.recv(4096).decode("utf-8", errors="replace")
                    if not data:
                        break
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        self._stdout_queue.put(line + "\n")
                else:
                    time.sleep(0.02)
            except Exception:
                break
        if buf:
            self._stdout_queue.put(buf)

    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]:
        with self._lock:
            if not command.strip():
                return {"stdout": "", "exit_code": 0, "cwd": self.cwd}

            t = float(timeout) if timeout is not None else self.default_timeout
            t = max(1.0, min(t, _MAX_TIMEOUT))

            if self._shell_channel is None or self._shell_channel.closed:
                try:
                    self._connect()
                except Exception as e:
                    return {"error": f"SSH connection failed: {e}", "exit_code": -1, "cwd": self.cwd}

            assert self._shell_channel is not None

            # Drain any stale output before running
            while not self._stdout_queue.empty():
                try:
                    self._stdout_queue.get_nowait()
                except queue.Empty:
                    break

            self._abort.clear()
            marker_uuid = uuid.uuid4().hex
            marker_sentinel = f"__CW_SSH_{marker_uuid}__"

            # Execute command followed by trailer
            trailer = f'printf "\\n%s %s %s\\n" "{marker_sentinel}" "$?" "$PWD"'
            full_payload = f"{command}\n{trailer}\n"
            try:
                self._shell_channel.send(full_payload.encode("utf-8"))
            except Exception as e:
                return {"error": f"Failed to send command over SSH: {e}", "exit_code": -1, "cwd": self.cwd}

            deadline = time.time() + t
            collected_lines: list[str] = []
            exit_code = 0
            saw_marker = False

            while time.time() < deadline and not self._abort.is_set():
                try:
                    line = self._stdout_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if marker_sentinel in line:
                    parts = line.strip().split()
                    for idx, p in enumerate(parts):
                        if p == marker_sentinel and idx + 2 < len(parts):
                            try:
                                exit_code = int(parts[idx + 1])
                                self.cwd = parts[idx + 2]
                                saw_marker = True
                                break
                            except ValueError:
                                pass
                    if saw_marker:
                        break
                else:
                    # Filter echo of the trailer command itself if present
                    if marker_sentinel not in line and 'printf "\\n%s' not in line:
                        collected_lines.append(line)

            if not saw_marker:
                if self._abort.is_set():
                    self.interrupt()
                    return {"error": "SSH command aborted by user", "exit_code": 130, "cwd": self.cwd}
                self.interrupt()
                return {"error": f"SSH command timed out after {t:.1f}s", "exit_code": 124, "cwd": self.cwd}

            output = "".join(collected_lines)
            if len(output) > self.max_output_chars:
                half = self.max_output_chars // 2
                output = (
                    output[:half]
                    + f"\n... [truncated {len(output) - self.max_output_chars} chars] ...\n"
                    + output[-half:]
                )

            return {
                "stdout": output,
                "exit_code": exit_code,
                "cwd": self.cwd,
            }

    def run_background(self, command: str) -> dict[str, Any]:
        with self._lock:
            if self._client is None:
                try:
                    self._connect()
                except Exception as e:
                    return {"error": f"SSH connection failed: {e}"}
            assert self._client is not None
            self._bg_counter += 1
            task_id = f"ssh_bg_{self._bg_counter}_{uuid.uuid4().hex[:6]}"
            task = _SSHBackgroundTask(task_id, command, self._client, self.cwd)
            self._bg_tasks[task_id] = task
            return {
                "task_id": task_id,
                "command": command,
                "status": "running",
                "cwd": self.cwd,
            }

    def background_output(self, task_id: str) -> dict[str, Any]:
        task = self._bg_tasks.get(task_id)
        if task is None:
            return {"error": f"no such background task: {task_id}"}
        output = task.read_new()
        return {
            "task_id": task_id,
            "output": output,
            "is_alive": task._is_alive,
        }

    def background_kill(self, task_id: str) -> dict[str, Any]:
        task = self._bg_tasks.get(task_id)
        if task is None:
            return {"error": f"no such background task: {task_id}"}
        task.kill()
        return {"task_id": task_id, "status": "killed"}

    def interrupt(self) -> None:
        self._abort.set()
        if self._shell_channel is not None and not self._shell_channel.closed:
            try:
                # Send ASCII Ctrl+C (ETX = \x03)
                self._shell_channel.send(b"\x03")
            except Exception:
                pass

    def close(self) -> None:
        if self._shell_channel is not None:
            try:
                self._shell_channel.close()
            except Exception:
                pass
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


class RemoteWorkspaceBackend:
    """SFTP file and remote directory operations manager."""

    def __init__(self, config: SSHConnectionConfig) -> None:
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._lock = threading.Lock()

    def _ensure_sftp(self) -> paramiko.SFTPClient:
        with self._lock:
            if self._sftp is not None:
                try:
                    self._sftp.stat(self.config.remote_cwd)
                    return self._sftp
                except Exception:
                    self._sftp = None

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            key_filename = None
            if self.config.key_path:
                kp = Path(self.config.key_path).expanduser().resolve()
                if kp.is_file():
                    key_filename = str(kp)

            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                key_filename=key_filename,
                passphrase=self.config.key_passphrase,
                timeout=self.config.connect_timeout,
            )
            self._client = client
            self._sftp = client.open_sftp()
            return self._sftp

    def read_file(self, rel_path: str, start_line: int = 1, max_lines: int = 2000) -> dict[str, Any]:
        sftp = self._ensure_sftp()
        full_path = posixpath.normpath(posixpath.join(self.config.remote_cwd, rel_path))
        if not full_path.startswith(self.config.remote_cwd):
            return {"error": "path escapes the remote workspace"}

        try:
            with sftp.open(full_path, "r") as fh:
                content = fh.read().decode("utf-8", errors="replace")
                lines = content.splitlines()
                total = len(lines)
                selected = lines[start_line - 1 : start_line - 1 + max_lines]
                numbered = [f"{i}\t{line}" for i, line in enumerate(selected, start=start_line)]
                return {
                    "lines": numbered,
                    "total_lines": total,
                    "shown": len(selected),
                    "start_line": start_line,
                    "has_more": (start_line - 1 + len(selected)) < total,
                }
        except Exception as e:
            return {"error": f"Failed to read remote file: {e}"}

    def write_file(self, rel_path: str, content: str) -> dict[str, Any]:
        sftp = self._ensure_sftp()
        full_path = posixpath.normpath(posixpath.join(self.config.remote_cwd, rel_path))
        if not full_path.startswith(self.config.remote_cwd):
            return {"error": "path escapes the remote workspace"}

        try:
            parent_dir = posixpath.dirname(full_path)
            self._mkdir_p(sftp, parent_dir)
            with sftp.open(full_path, "w") as fh:
                fh.write(content.encode("utf-8"))
            return {"status": "ok", "path": rel_path}
        except Exception as e:
            return {"error": f"Failed to write remote file: {e}"}

    def _mkdir_p(self, sftp: paramiko.SFTPClient, remote_directory: str) -> None:
        dirs = []
        d = remote_directory
        while len(d) > 1:
            dirs.append(d)
            d, _ = posixpath.split(d)
        if len(d) == 1 and d[0] == "/":
            dirs.append("/")

        while dirs:
            current = dirs.pop()
            try:
                sftp.stat(current)
            except Exception:
                try:
                    sftp.mkdir(current)
                except Exception:
                    pass

    def test_connection(self) -> dict[str, Any]:
        try:
            t0 = time.time()
            sftp = self._ensure_sftp()
            sftp.stat(self.config.remote_cwd)
            latency_ms = int((time.time() - t0) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "host": self.config.host}
        except Exception as e:
            return {"ok": False, "error": str(e), "host": self.config.host}

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
