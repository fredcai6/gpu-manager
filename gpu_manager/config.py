"""Broker configuration: defaults, overridden by ~/.config/gpu-manager/config.toml."""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024
PATHS = ("state_dir", "compose_file", "env_file")


@dataclasses.dataclass(frozen=True)
class Config:
    state_dir: Path = Path.home() / ".local/state/gpu-manager"
    # The compute card's PCI address; unset means the AMD card with the most VRAM.
    pci_id: Optional[str] = None
    # Headroom kept free on top of every declared footprint.
    margin_mib: int = 1024
    # Measured 2026-11-03: 27B 22.4 GB + ASR 6.6 GB = 29391 MiB.
    pair_vram_mib: int = 30000
    # Batch may take the card once no interactive request has arrived for this long.
    hold_minutes: float = 30.0
    # Off: an interactive request waits for running batch work instead of evicting it.
    preempt_on_interactive: bool = False
    compose_file: Path = REPO / "interactive/docker-compose.yml"
    # Env file passed to `docker compose` for the pair (model files, paths, aliases).
    env_file: Optional[Path] = None
    # Public ports the proxy owns, and where the servers listen behind it.
    llm_port: int = 8005
    asr_port: int = 8006
    llm_backend: int = 18005
    asr_backend: int = 18006
    # Addresses the proxy listens on. Loopback callers count as batch; add the Docker
    # bridge gateways that interactive clients (the gate, host.docker.internal) arrive on.
    listen: Tuple[str, ...] = ("127.0.0.1",)
    # How long a caller's request waits for the servers before a 503.
    queue_wait_seconds: float = 6 * 3600
    # Time an in-flight request gets to finish before the pair is stopped under it.
    drain_seconds: float = 120.0
    tick_seconds: float = 5.0
    # Directories the status page is written to (add a static site's directory to publish it).
    page_dirs: Tuple[str, ...] = ("~/.local/state/gpu-manager/page",)
    page_seconds: float = 30.0
    default_retries: int = 2
    default_grace_seconds: int = 60

    @property
    def db_path(self) -> Path:
        return self.state_dir / "broker.db"

    def job_dir(self, job_id: int) -> Path:
        return self.state_dir / "jobs" / str(job_id)


def load(path: Optional[Path] = None) -> Config:
    path = path or Path(os.environ.get(
        "GPU_MANAGER_CONFIG", Path.home() / ".config/gpu-manager/config.toml"))
    if not Path(path).exists():
        return Config()
    raw = tomllib.loads(Path(path).read_text())
    fields = {f.name: f for f in dataclasses.fields(Config)}
    kwargs = {}
    for key, value in raw.items():
        if key not in fields:
            raise ValueError(f"unknown config key {key!r} in {path}")
        default = getattr(Config, key)
        if key in PATHS:
            value = Path(value).expanduser()
        elif isinstance(default, tuple):
            value = tuple(value)
        kwargs[key] = value
    return Config(**kwargs)
