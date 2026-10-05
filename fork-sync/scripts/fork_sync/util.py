"""Small helpers shared by the fork-sync commands: running commands, logging,
and talking to the GitHub Actions runner (outputs, summaries, masks)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping, Sequence


class SyncError(Exception):
    """A failure that stops the current command with a readable message."""


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    if in_actions():
        print(f"::warning::{_escape_annotation(msg)}", flush=True)
    else:
        print(f"warning: {msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    if in_actions():
        print(f"::error::{_escape_annotation(msg)}", flush=True)
    else:
        print(f"error: {msg}", file=sys.stderr, flush=True)


def _escape_annotation(msg: str) -> str:
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def mask(value: str) -> None:
    """Ask the Actions runner to hide `value` in logs."""
    if value and in_actions():
        print(f"::add-mask::{value}", flush=True)


def group(title: str):
    class _Group:
        def __enter__(self):
            if in_actions():
                print(f"::group::{title}", flush=True)
            else:
                print(f"--- {title}", flush=True)

        def __exit__(self, *exc):
            if in_actions():
                print("::endgroup::", flush=True)
            return False

    return _Group()


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        if "\n" in value:
            delim = "EOF_fork_sync_output"
            f.write(f"{name}<<{delim}\n{value}\n{delim}\n")
        else:
            f.write(f"{name}={value}\n")


def summary(markdown: str) -> None:
    """Append to the job summary, or print it when run locally."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown.rstrip() + "\n\n")
    else:
        log("\n===== job summary =====\n" + markdown.rstrip() + "\n=======================\n")


def run(
    cmd: Sequence[str],
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
    quiet: bool = False,
    timeout: float | None = None,
    input: str | None = None,
    base_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command without a shell. `env` entries are added to `base_env`
    (default: the current environment); they are never printed."""
    full_env = dict(os.environ if base_env is None else base_env)
    if env:
        full_env.update(env)
    if not quiet:
        log("+ " + " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=full_env,
        text=True,
        capture_output=capture,
        timeout=timeout,
        input=input,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise SyncError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{detail[-4000:]}")
    return proc


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "[… truncated …]\n" + text[-max_chars:]


def head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[… truncated …]"


def run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if repo and run_id:
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
        suffix = f"/attempts/{attempt}" if attempt and attempt != "1" else ""
        return f"{server}/{repo}/actions/runs/{run_id}{suffix}"
    return "(local run)"


def scrubbed_env(extra_drop: Iterable[str] = ()) -> dict:
    """The current environment without credentials, for running third-party
    code (checks) or Claude."""
    drop_prefixes = ("GITHUB_TOKEN", "GH_TOKEN", "FORK_SYNC_TOKEN", "ACTIONS_", "SLACK_")
    drop_contains = ("PRIVATE_KEY", "WEBHOOK")
    env = {}
    for k, v in os.environ.items():
        if k.startswith(drop_prefixes) or any(s in k for s in drop_contains):
            continue
        if k in extra_drop:
            continue
        env[k] = v
    return env
