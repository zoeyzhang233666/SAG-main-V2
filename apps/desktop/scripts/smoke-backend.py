"""Probe the frozen API against an isolated empty database."""
import os
import pathlib
import socket
import subprocess
import tempfile
import time
import urllib.request

root = pathlib.Path(__file__).resolve().parents[2] / "api" / "dist" / "desktop" / "sag-api"
exe = root / ("sag-api.exe" if os.name == "nt" else "sag-api")
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
with tempfile.TemporaryDirectory() as work:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAG_")}
    env.update(SAG_ENVIRONMENT="prod", SAG_SECRET_KEY=os.urandom(48).hex(),
               SAG_DESKTOP_HOST="127.0.0.1", SAG_DESKTOP_PORT=str(port),
               SAG_DATABASE_URL="sqlite+aiosqlite:///./.data/sag.db",
               SAG_DATA_DIR="./.data/engine", SAG_UPLOAD_DIR="./.data/uploads",
               SAG_SAG_VECTOR_PROVIDER="lancedb")
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen([str(exe)], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(120):
                if proc.poll() is not None:
                    raise RuntimeError(f"Frozen API exited: {proc.returncode}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/system/ready", timeout=2) as response:
                        if response.status == 200:
                            print("Frozen API readiness passed")
                            break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Frozen API readiness timed out")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            log.seek(0)
            print(log.read().decode("utf-8", errors="replace"))
