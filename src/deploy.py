"""Launcher de deploy para correr API, UI o ambos servicios."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from collections.abc import Sequence


def _api_command() -> list[str]:
    port = os.getenv("PORT", "8000")
    os.environ.setdefault("FINSAGE_API_HOST", "0.0.0.0")
    os.environ.setdefault("FINSAGE_API_PORT", port)
    return [sys.executable, "-m", "src.api.main"]


def _ui_command() -> list[str]:
    port = os.getenv("PORT", "8501")
    os.environ.setdefault("FINSAGE_API_URL", "http://127.0.0.1:8000")
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "src/ui/app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]


def _combined_commands() -> tuple[list[str], list[str]]:
    os.environ.setdefault("FINSAGE_API_HOST", "127.0.0.1")
    os.environ.setdefault("FINSAGE_API_PORT", "8000")
    os.environ.setdefault("FINSAGE_API_URL", "http://127.0.0.1:8000")
    return [sys.executable, "-m", "src.api.main"], _ui_command()


def _run_foreground(command: Sequence[str]) -> int:
    return subprocess.run(list(command), check=False).returncode


def _run_combined() -> int:
    api_command, ui_command = _combined_commands()
    api_proc = subprocess.Popen(api_command)
    ui_proc = subprocess.Popen(ui_command)

    def _terminate_children(*_: object) -> None:
        for proc in (ui_proc, api_proc):
            if proc.poll() is None:
                proc.terminate()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _terminate_children)
    signal.signal(signal.SIGINT, _terminate_children)

    try:
        while True:
            api_code = api_proc.poll()
            ui_code = ui_proc.poll()
            if api_code is not None:
                if ui_proc.poll() is None:
                    ui_proc.terminate()
                    ui_proc.wait(timeout=10)
                return api_code
            if ui_code is not None:
                if api_proc.poll() is None:
                    api_proc.terminate()
                    api_proc.wait(timeout=10)
                return ui_code
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service",
        choices=("api", "ui", "all"),
        default=os.getenv("FINSAGE_SERVICE", "all"),
        help="Servicio a lanzar dentro del contenedor.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.service == "api":
        return _run_foreground(_api_command())
    if args.service == "ui":
        return _run_foreground(_ui_command())
    return _run_combined()


if __name__ == "__main__":
    raise SystemExit(main())
