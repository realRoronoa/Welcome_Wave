"""Render entrypoint: run idempotent migrations in self-hosted mode, then exec uvicorn."""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    if os.environ.get("MODE") == "self_hosted" and os.environ.get("DATABASE_URL_OWNER"):
        subprocess.run([sys.executable, "-m", "app.migrate"], check=True)
    port = os.environ.get("PORT", "8080")
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--workers",
            "1",
            "--proxy-headers",
            "--timeout-graceful-shutdown",
            "20",
        ],
    )


if __name__ == "__main__":
    main()
