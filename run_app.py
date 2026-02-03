from __future__ import annotations

import shutil
import subprocess
import sys
import time


def _start_process(command: list[str], cwd: str) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=cwd)


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

    frontend_cmd = ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

    frontend_available = shutil.which("npm") is not None

    backend = _start_process(backend_cmd, cwd="backend")
    frontend = None
    if frontend_available:
        frontend = _start_process(frontend_cmd, cwd="frontend")
    else:
        print("npm is not available; frontend will not be started.", file=sys.stderr)

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
