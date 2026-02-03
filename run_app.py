from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _start_process(command: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=str(cwd))


def _find_npm() -> str | None:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm")
    return shutil.which("npm")


def _resolve_dir(name: str) -> Path:
    script_root = Path(__file__).resolve().parent
    return (script_root / name).resolve()


def main() -> int:
    backend_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]

    npm_executable = _find_npm()
    frontend_cmd = [
        npm_executable or "npm",
        "run",
        "dev",
        "--",
        "--host",
        "0.0.0.0",
        "--port",
        "5173",
    ]

    frontend_available = npm_executable is not None
    backend_dir = _resolve_dir("backend")
    frontend_dir = _resolve_dir("frontend")

    if not backend_dir.exists():
        print(f"Backend directory not found: {backend_dir}", file=sys.stderr)
        return 1

    if frontend_available and not frontend_dir.exists():
        print(f"Frontend directory not found: {frontend_dir}", file=sys.stderr)
        return 1

    backend = _start_process(backend_cmd, cwd=backend_dir)
    frontend = None
    if frontend_available:
        frontend = _start_process(frontend_cmd, cwd=frontend_dir)
    else:
        print(
            "npm executable not found; frontend will not be started.",
            file=sys.stderr,
        )

    try:
        while True:
            if backend.poll() is not None:
                print("Backend process stopped.", file=sys.stderr)
                return 1
            if frontend is not None and frontend.poll() is not None:
                print("Frontend process stopped.", file=sys.stderr)
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...", file=sys.stderr)
    finally:
        for process in (frontend, backend):
            if process and process.poll() is None:
                process.terminate()
        for process in (frontend, backend):
            if process and process.poll() is None:
                process.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
