"""Tests unitarios del launcher de deploy."""

from __future__ import annotations

import signal
from typing import Any

from src import deploy


class _DummyCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class _DummyProcess:
    def __init__(self, codes: list[int | None]) -> None:
        self._codes = list(codes)
        self.terminated = False
        self.wait_calls: list[int | None] = []

    def poll(self) -> int | None:
        if len(self._codes) > 1:
            return self._codes.pop(0)
        return self._codes[0]

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int | None = None) -> None:
        self.wait_calls.append(timeout)


def test_api_command_uses_port_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.delenv("FINSAGE_API_HOST", raising=False)
    monkeypatch.delenv("FINSAGE_API_PORT", raising=False)

    command = deploy._api_command()

    assert command == ["python", "-m", "src.api.main"] or command == [
        deploy.sys.executable,
        "-m",
        "src.api.main",
    ]
    assert deploy.os.environ["FINSAGE_API_HOST"] == "0.0.0.0"
    assert deploy.os.environ["FINSAGE_API_PORT"] == "9000"


def test_ui_command_sets_streamlit_defaults(monkeypatch: Any) -> None:
    monkeypatch.setenv("PORT", "8505")
    monkeypatch.delenv("FINSAGE_API_URL", raising=False)

    command = deploy._ui_command()

    assert command[:3] == [deploy.sys.executable, "-m", "streamlit"]
    assert "--server.address=0.0.0.0" in command
    assert "--server.port=8505" in command
    assert deploy.os.environ["FINSAGE_API_URL"] == "http://127.0.0.1:8000"


def test_combined_commands_force_internal_api_url(monkeypatch: Any) -> None:
    monkeypatch.delenv("FINSAGE_API_HOST", raising=False)
    monkeypatch.delenv("FINSAGE_API_PORT", raising=False)
    monkeypatch.delenv("FINSAGE_API_URL", raising=False)

    api_command, ui_command = deploy._combined_commands()

    assert api_command == [deploy.sys.executable, "-m", "src.api.main"]
    assert ui_command[:3] == [deploy.sys.executable, "-m", "streamlit"]
    assert deploy.os.environ["FINSAGE_API_HOST"] == "127.0.0.1"
    assert deploy.os.environ["FINSAGE_API_PORT"] == "8000"
    assert deploy.os.environ["FINSAGE_API_URL"] == "http://127.0.0.1:8000"


def test_run_foreground_returns_subprocess_code(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda command, check: _DummyCompleted(7),
    )

    code = deploy._run_foreground(["echo", "hola"])

    assert code == 7


def test_run_combined_terminates_other_process_when_api_exits(monkeypatch: Any) -> None:
    api_proc = _DummyProcess([0])
    ui_proc = _DummyProcess([None, None])
    created: list[_DummyProcess] = [api_proc, ui_proc]

    monkeypatch.setattr(deploy, "_combined_commands", lambda: (["api"], ["ui"]))
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda command: created.pop(0))

    code = deploy._run_combined()

    assert code == 0
    assert ui_proc.terminated is True
    assert ui_proc.wait_calls == [10]


def test_main_routes_to_selected_service(monkeypatch: Any) -> None:
    monkeypatch.setattr(deploy, "_run_foreground", lambda command: 11)
    monkeypatch.setattr(deploy, "_api_command", lambda: ["api"])
    monkeypatch.setattr(deploy, "_ui_command", lambda: ["ui"])
    monkeypatch.setattr(deploy, "_run_combined", lambda: 22)

    assert deploy.main(["--service", "api"]) == 11
    assert deploy.main(["--service", "ui"]) == 11
    assert deploy.main(["--service", "all"]) == 22


def test_run_combined_restores_signal_handlers(monkeypatch: Any) -> None:
    api_proc = _DummyProcess([None, 0])
    ui_proc = _DummyProcess([0])
    created: list[_DummyProcess] = [api_proc, ui_proc]
    handlers: dict[int, Any] = {
        signal.SIGTERM: object(),
        signal.SIGINT: object(),
    }

    monkeypatch.setattr(deploy, "_combined_commands", lambda: (["api"], ["ui"]))
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda command: created.pop(0))
    monkeypatch.setattr(deploy.signal, "getsignal", lambda sig: handlers[sig])

    restored: list[tuple[int, Any]] = []

    def _fake_signal(sig: int, handler: Any) -> None:
        restored.append((sig, handler))

    monkeypatch.setattr(deploy.signal, "signal", _fake_signal)

    code = deploy._run_combined()

    assert code == 0
    assert restored[-2:] == [
        (signal.SIGTERM, handlers[signal.SIGTERM]),
        (signal.SIGINT, handlers[signal.SIGINT]),
    ]
