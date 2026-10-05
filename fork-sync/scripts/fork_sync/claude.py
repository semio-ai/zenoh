"""Running Claude Code headless in the carry clone."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .util import SyncError, group, head, log, scrubbed_env, warn

PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "resolve-conflicts.md"

DEFAULT_MODEL = "claude-fable-5-1"

# File tools plus the shell commands a rebase needs. Everything else is denied
# (--permission-mode dontAsk turns every other request into a refusal).
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep", "TodoWrite",
    "Bash(git *)", "Bash(cargo *)",
    "Bash(ls)", "Bash(ls *)", "Bash(pwd)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
    "Bash(wc *)", "Bash(diff *)", "Bash(grep *)", "Bash(rg *)", "Bash(sort *)", "Bash(uniq *)",
]
DISALLOWED_TOOLS = [
    "WebFetch", "WebSearch", "Agent", "Task",
    "Bash(git push)", "Bash(git push *)", "Bash(git fetch *)", "Bash(git pull *)",
    "Bash(git remote *)", "Bash(git config *)", "Bash(git credential *)", "Bash(git submodule *)",
    "Bash(cargo install *)", "Bash(cargo publish *)", "Bash(cargo login *)", "Bash(cargo owner *)",
]

CONFLICT_TASK = """\
`git rebase` stopped on a conflict while replaying Semio commit {commit} \
("{subject}"). Conflicting files:

{files}

Resolve this conflict and every later one, following the rules above, until the
rebase completes on `carry/{new}`. Then, if time allows, run the targeted tests
listed in SEMIO.md's "Carrying" section and fix what your resolutions broke."""

FIX_TASK = """\
The rebase of `carry/{new}` completed{how}, but these checks fail on it:

{failures}

The logs are in `{logs_dir}` (read them with the Read tool). Find the cause.
If a Semio commit, or an adaptation of one, causes the failure, fix it in the
commit it belongs to (`git commit --fixup=<commit>`, then
`git rebase -i --autosquash {new}`) and run the failing checks again. If the
failure comes from upstream code that Semio's commits do not touch (it would
fail on the tag `{new}` alone), or from the environment, do not patch upstream:
write `{abort_path}` explaining it instead."""


def render_prompt(*, repo, upstream, old, new, task_summary, task, report_path, abort_path, repo_dir) -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    for k, v in {
        "REPO": repo, "UPSTREAM": upstream, "OLD": old, "NEW": new, "REPO_DIR": str(repo_dir),
        "TASK_SUMMARY": task_summary, "TASK": task,
        "REPORT_PATH": str(report_path), "ABORT_PATH": str(abort_path),
    }.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def run_claude(*, repo_dir: Path, out_dir: Path, mode: str, system_prompt: str, user_prompt: str,
               max_turns: int, timeout_minutes: int, report_path: Path, abort_path: Path) -> dict:
    """Run one Claude Code session. Returns what the result event says."""
    claude = os.environ.get("FORK_SYNC_CLAUDE_CMD") or shutil.which("claude")
    if not claude:
        raise SyncError("the claude CLI is not installed")
    model = os.environ.get("FORK_SYNC_MODEL") or DEFAULT_MODEL
    prompt_path = out_dir / f"claude-{mode}-prompt.md"
    prompt_path.write_text(system_prompt, encoding="utf-8")
    transcript = out_dir / f"claude-{mode}.jsonl"
    # Claude Code's subprocess sandbox write-protects some paths of its launch
    # directory (.github/workflows among them) and drops placeholder files
    # there (.env, .gitmodules, ...). Launch it from an empty directory and
    # give it the repository with --add-dir, so that neither touches the clone.
    launch_dir = out_dir.parent / "claude-launch"
    launch_dir.mkdir(exist_ok=True)

    cmd = [
        claude, "-p", user_prompt,
        "--append-system-prompt-file", str(prompt_path),
        "--model", model,
        "--max-turns", str(max_turns),
        "--permission-mode", "dontAsk",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--add-dir", str(repo_dir), str(out_dir),
        # Ignore the repository's .claude/ settings, hooks and MCP servers.
        "--setting-sources", "user",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "stream-json", "--verbose",
    ]
    if os.environ.get("FORK_SYNC_CLAUDE_BARE", "") == "1":
        cmd.append("--bare")  # also skips CLAUDE.md discovery; auth only via ANTHROPIC_API_KEY
    budget = os.environ.get("FORK_SYNC_CLAUDE_MAX_BUDGET_USD")
    if budget:
        cmd += ["--max-budget-usd", budget]

    # Never let the child join a parent Claude Code session (when run locally
    # from inside one): no messaging socket, no session ids.
    env = {k: v for k, v in scrubbed_env().items()
           if not k.startswith(("CLAUDE_CODE_MESSAGING", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_REMOTE_SESSION",
                                "CLAUDE_SESSION", "CLAUDE_PID")) and k != "CLAUDECODE"}
    env.update({
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_TERMINAL_PROMPT": "0",
        # Strip ANTHROPIC_API_KEY and other credentials from the commands
        # Claude runs (cargo runs upstream build scripts).
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "BASH_DEFAULT_TIMEOUT_MS": str(45 * 60 * 1000),
        "BASH_MAX_TIMEOUT_MS": str(90 * 60 * 1000),
        "FORK_SYNC_MODE": mode,
        "FORK_SYNC_REPORT_PATH": str(report_path),
        "FORK_SYNC_ABORT_PATH": str(abort_path),
        "FORK_SYNC_REPO_DIR": str(repo_dir),
    })

    started = time.monotonic()
    result: dict = {}
    log(f"running Claude Code ({mode}, model {model}, at most {max_turns} turns, {timeout_minutes} min)")
    with group(f"Claude Code transcript ({mode})"), transcript.open("w", encoding="utf-8") as tf:
        proc = subprocess.Popen(cmd, cwd=launch_dir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        deadline = started + timeout_minutes * 60
        timed_out = False
        assert proc.stdout is not None
        for line in proc.stdout:
            tf.write(line)
            _echo(line, result)
            if time.monotonic() > deadline:
                timed_out = True
                proc.kill()
                break
        proc.wait()
    info = {
        "mode": mode,
        "model": model,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started),
        "turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "final_message": head(str(result.get("result") or ""), 3000),
        "permission_denials": len(result.get("permission_denials") or []),
        "stderr_tail": "\n".join(result.get("_other", [])),
    }
    if timed_out:
        warn(f"Claude Code ({mode}) hit the {timeout_minutes}-minute timeout")
    log(f"Claude Code finished: {json.dumps({k: info[k] for k in ('exit_code', 'subtype', 'turns', 'cost_usd', 'seconds')})}")
    return info


def _echo(line: str, result: dict) -> None:
    """Print a one-line digest of each stream-json event to the job log."""
    try:
        ev = json.loads(line)
    except ValueError:
        log(f"[claude] {line.rstrip()[:300]}")
        if line.strip():
            result.setdefault("_other", []).append(line.rstrip()[:500])
            del result["_other"][:-15]
        return
    t = ev.get("type")
    if t == "result":
        result.update(ev)
        return
    if t != "assistant":
        return
    for block in (ev.get("message") or {}).get("content") or []:
        if block.get("type") == "tool_use":
            inp = block.get("input") or {}
            arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
            log(f"[claude] {block.get('name')}: {str(arg)[:300]}")
        elif block.get("type") == "text" and block.get("text", "").strip():
            log(f"[claude] {block['text'].strip()[:500]}")
