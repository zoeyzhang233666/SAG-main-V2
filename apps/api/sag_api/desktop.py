"""Desktop sidecar entry point."""

from __future__ import annotations

import os
from threading import Thread

import uvicorn


def _port() -> int:
    value = os.getenv("SAG_DESKTOP_PORT", "18100")
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("SAG_DESKTOP_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("SAG_DESKTOP_PORT must be between 1 and 65535")
    return port


def main() -> None:
    config = uvicorn.Config(
        "sag_api.main:app",
        host=os.getenv("SAG_DESKTOP_HOST", "127.0.0.1"),
        port=_port(),
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    def watch_parent() -> None:
        try:
            while os.read(0, 1):
                pass
        finally:
            server.should_exit = True

    # Bind before running lifespan. A failed listener must never recover jobs.
    with config.bind_socket() as listener:
        if os.getenv("SAG_DESKTOP_WATCH_STDIN") == "1":
            Thread(target=watch_parent, daemon=True, name="electron-lifeline").start()
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
