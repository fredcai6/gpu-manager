# gpu-manager

Shares one AMD GPU between an always-wanted interactive tier (a local LLM and a
speech-recognition server) and batch jobs. A broker queues batch jobs, lends them
the card when the interactive tier is idle, and fronts the LLM/ASR ports so callers
wait instead of failing while the card is lent out. Python standard library only;
Linux, AMDGPU, Docker and systemd user units.

## Using it

```
gpu status [--json]                   card snapshot plus broker state
gpu submit --vram 18G --name NAME -- CMD...
gpu queue [--json]                    broker state only
gpu cancel ID
gpu log ID                            path of a job's log
gpu take / gpu release                operator override: stop everything / resume
```

A submitted job runs from the directory it
was submitted in, with the submitter's environment.

### The job contract

- **Save progress about every 10 minutes and resume from it.** A job can be stopped
  at any moment (operator take, reboot, power loss) and is simply rerun with the
  same command.
- **Stop on SIGTERM.** The job gets its grace period (`--grace`, default 60 s)
  before SIGKILL.
- **Label containers:** add `--label "$GPU_JOB_LABEL"` to `docker run`, so a
  stopped job cannot leave a container holding VRAM.
- **Declare peak VRAM** with `--vram`. A job starts only when both the measured
  free VRAM and the declared budget have room.
- **Report progress** by printing `chunk N/M` (optionally with `NN%`) on a line, or
  by calling `gpu progress --chunk N/M --pct P`. Keep chunks under 4 hours.
- **Failures:** a failing exit is retried up to `--retries` times (default 2).
  A stop, a reboot or a power loss is an interruption: the job goes back to the
  front of the queue, and it doesn't count as a failure.

The status page shows the card, who holds its VRAM, each running job's progress and
last output line, the queue, finished jobs and the last day of events. The broker
rewrites it every 30 s into each directory in `page_dirs`.

## How the card is shared

- **The interactive pair:** `interactive-llm-1` (a 27B model, :8005) and
  `interactive-asr-1` (:8006), serving chat, voice notes and tutoring clients.
- **Batch takes the card** once no interactive request has arrived for 30 minutes.
  The broker lets in-flight requests finish (up to 2 minutes), stops the pair, and
  runs jobs first come, first served. A later job may start beside others only if it
  doesn't delay the job at the head of the queue.
- **The pair comes back** when the queue is empty and it fits.
- **No preemption:** an interactive request does not evict batch work
  (`preempt_on_interactive = false`).
- **Requests wait:** any request to :8005/:8006 waits while the pair is down, and
  goes through once it is back. `/health` answers 503 at once instead of waiting.
- **Tiers by origin:** loopback callers (scripts, pipelines) are batch. Requests from
  Docker bridges (clients via the `gate` container or `host.docker.internal`) are
  interactive. Only interactive requests reset the hold.

Measured on a 32 GB Radeon AI PRO R9700: the 27B (Q4_K_M, 64k context) takes
22.4 GB and the ASR model 6.6 GB, so the pair holds 29.2 GB and leaves about 3 GB
beside it. The 27B loads in about 22 s. `docker pause` frees no VRAM, so a paused
job still blocks the card.

## Layout

| Path | What |
|---|---|
| `gpu_manager/scheduler.py` | the scheduling rules, as one pure function |
| `gpu_manager/daemon.py` | the broker loop (`gpu daemon`) |
| `gpu_manager/proxy.py` | the :8005/:8006 front door |
| `gpu_manager/runner.py` | jobs as transient systemd user units (`gpu-job-N`) |
| `gpu_manager/board.py`, `page.py` | broker state for `gpu status` and the status page |
| `gpu_manager/store.py` | state in `~/.local/state/gpu-manager/broker.db` |
| `gpu_manager/config.py` | defaults; override in `~/.config/gpu-manager/config.toml` |
| `interactive/docker-compose.yml` | the pair, plus `gate`, which holds the `llm`/`asr` names on Journal's network |
| `interactive/check-journal.sh` | end-to-end chat and voice-note check through Journal |
| `deploy/gpu-manager.service` | systemd user unit |
| `deploy/config.example.toml` | every setting, for `~/.config/gpu-manager/config.toml` |

## Setup

```
ln -s "$PWD/gpu" ~/.local/bin/gpu
cp deploy/config.example.toml ~/.config/gpu-manager/config.toml   # then edit
ln -s "$PWD/deploy/gpu-manager.service" ~/.config/systemd/user/
systemctl --user enable --now gpu-manager
loginctl enable-linger "$USER"     # so the broker and jobs run without a login
```

Start the pair once with `docker compose -f interactive/docker-compose.yml
--env-file ENV --profile llm --profile asr up -d --no-build llm asr gate`
(build first if you have no `docker-llm` image); the broker manages it from then on.

## Operating

```
systemctl --user status gpu-manager        # broker
journalctl --user -u gpu-manager -f
systemctl --user restart gpu-manager       # jobs keep running; they are their own units
```

Restart the pair by hand only with the broker in a take, or it will undo you:
`gpu take`, do the work, `gpu release`.

## Tests

```
python3 -m pytest -q tests
```
