"""Tests for SSH Executor and Remote Workspace Backend."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from coworker.tools.ssh import (
    SSHConnectionConfig,
    SSHExecutor,
    RemoteWorkspaceBackend,
)


def test_ssh_config_from_dict():
    data = {
        "host": "192.168.1.100",
        "port": 2222,
        "username": "ubuntu",
        "password": "secretpassword",
        "remote_cwd": "/home/ubuntu/app",
        "connect_timeout": 10.0,
    }
    cfg = SSHConnectionConfig.from_dict(data)
    assert cfg.host == "192.168.1.100"
    assert cfg.port == 2222
    assert cfg.username == "ubuntu"
    assert cfg.password == "secretpassword"
    assert cfg.remote_cwd == "/home/ubuntu/app"
    assert cfg.connect_timeout == 10.0


@patch("paramiko.SSHClient")
def test_ssh_executor_run_mocked(mock_ssh_client_cls):
    mock_client = MagicMock()
    mock_ssh_client_cls.return_value = mock_client
    mock_channel = MagicMock()
    mock_client.invoke_shell.return_value = mock_channel
    mock_channel.closed = False
    mock_channel.recv_ready.side_effect = [True, False, False]
    mock_channel.recv.return_value = b"Hello from remote VPS\n"

    cfg = SSHConnectionConfig(host="10.0.0.1")

    # Mocking internal drain & channel
    with patch.object(SSHExecutor, "_connect"):
        executor = SSHExecutor(cfg)
        executor._shell_channel = mock_channel
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "test"

            def inject_output(*args, **kwargs):
                executor._stdout_queue.put("Hello from remote\n")
                executor._stdout_queue.put("__CW_SSH_test__ 0 /root/workspace\n")

            mock_channel.send.side_effect = inject_output
            res = executor.run("echo hello", timeout=2.0)

            assert res["exit_code"] == 0
            assert res["cwd"] == "/root/workspace"
            assert "Hello from remote" in res["stdout"]


@patch("paramiko.SSHClient")
def test_remote_workspace_backend_test_connection(mock_ssh_client_cls):
    mock_client = MagicMock()
    mock_ssh_client_cls.return_value = mock_client
    mock_sftp = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp

    cfg = SSHConnectionConfig(host="10.0.0.1", remote_cwd="/var/app")
    backend = RemoteWorkspaceBackend(cfg)

    res = backend.test_connection()
    assert res["ok"] is True
    assert res["host"] == "10.0.0.1"
    assert "latency_ms" in res
