"""The interactive pair (27B on llm, transcriber on asr): health, start, stop."""

from __future__ import annotations

import subprocess
import urllib.request

from .config import Config


def _compose(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    env = ["--env-file", str(cfg.env_file)] if cfg.env_file else []
    return subprocess.run(
        ["docker", "compose", "-f", str(cfg.compose_file), *env,
         "--profile", "llm", "--profile", "asr", *args],
        capture_output=True, text=True)


def healthy(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def running(cfg: Config) -> bool:
    r = _compose(cfg, "ps", "--status", "running", "-q", "llm", "asr")
    return bool(r.stdout.split())


def state(cfg: Config) -> str:
    """up when both answer /health, starting when containers run but are not ready."""
    if healthy(cfg.llm_backend) and healthy(cfg.asr_backend):
        return "up"
    return "starting" if running(cfg) else "down"


def start(cfg: Config) -> None:
    r = _compose(cfg, "up", "-d", "--no-build", "llm", "asr")
    if r.returncode:
        raise RuntimeError(f"compose up failed: {r.stderr.strip()[-400:]}")


def stop(cfg: Config) -> None:
    r = _compose(cfg, "stop", "llm", "asr")
    if r.returncode:
        raise RuntimeError(f"compose stop failed: {r.stderr.strip()[-400:]}")
