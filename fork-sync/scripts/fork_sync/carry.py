"""Phase 2 (no GitHub credentials; runs upstream code and Claude): replay
semio/<old> onto the new tag, let Claude resolve conflicts, run the checks, and
leave a bundle of carry/<new> plus result.json for the publish job."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import semio_md
from .claude import CONFLICT_TASK, FIX_TASK, render_prompt, run_claude
from .git import Git
from .util import SyncError, group, head, log, read_json, run, scrubbed_env, tail, warn, write_json

CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")


def carry(work: Path, upstream_url: str, install_toolchain: bool = False,
          claude_timeout_minutes: int = 150, check_timeout_minutes: int = 90) -> dict:
    p = read_json(work / "plan.json")
    if p["action"] != "carry":
        raise SyncError(f"nothing to carry: {p['reason']}")
    fork = p["fork"]
    old, new = p["old"], p["new"]
    out = work / "out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    repo_dir = work / "repo"
    report_path, abort_path = out / "SYNC_REPORT.md", out / "SYNC_ABORT.md"

    res: dict = {
        "status": "failure", "reason": "", "repo": p["repo"], "old": old, "new": new,
        "new_sha": p["new_sha"], "conflicts": [], "claude_runs": [], "checks": [],
        "commits": {}, "drop_suggestions": [], "report_md": "", "abort_md": "",
    }

    def finish(status: str, reason: str) -> dict:
        res["status"], res["reason"] = status, reason
        if report_path.exists():
            res["report_md"] = report_path.read_text(encoding="utf-8", errors="replace")
        if abort_path.exists():
            res["abort_md"] = abort_path.read_text(encoding="utf-8", errors="replace")
        write_json(out / "result.json", res)
        log(f"carry: {status}: {reason}")
        return res

    # 1. Clone the public upstream and add semio/<old> from the plan's bundle.
    with group("clone"):
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        run(["git", "clone", "--quiet", "--origin", "upstream", "--no-checkout", upstream_url, str(repo_dir)],
            timeout=1800)
        g = Git(repo_dir)
        g("fetch", "--quiet", "--tags", "upstream")
        if g.rev(f"refs/tags/{new}") != p["new_sha"] or g.rev(f"refs/tags/{old}") != p["old_sha"]:
            raise SyncError("upstream tags moved since planning; run again")
        g("bundle", "verify", str(work / "input.bundle"))
        g("fetch", "--quiet", str(work / "input.bundle"), f"refs/heads/semio/{old}:refs/heads/semio/{old}")
        if g.rev(f"semio/{old}") != p["semio_old_sha"]:
            raise SyncError("the bundle does not hold the planned semio/<old>")
        g("branch", "--quiet", f"semio/{new}", f"refs/tags/{new}")
        for k, v in {
            "user.name": p["bot"]["name"], "user.email": p["bot"]["email"],
            "rerere.enabled": "true", "rerere.autoUpdate": "true",
            "merge.conflictStyle": "zdiff3", "core.hooksPath": "/dev/null", "commit.gpgSign": "false",
        }.items():
            g("config", k, v, quiet=True)
        # Keep Claude's notes and build output out of `git status`.
        with (g.git_dir() / "info" / "exclude").open("a") as f:
            f.write("\n/target/\nSYNC_REPORT.md\nSYNC_ABORT.md\n")

    semio_text = g.out("show", f"semio/{old}:SEMIO.md", check=False) or ""
    if not semio_text:
        warn(f"semio/{old} has no SEMIO.md")
    originals = commit_list(g, f"{old}..semio/{old}")
    res["commits"]["original"] = originals

    if install_toolchain:
        install_rust_toolchain(g, new)

    # 2. Rebase.
    with group(f"git rebase --onto {new} {old}"):
        g("switch", "--quiet", "-c", f"carry/{new}", f"semio/{old}")
        reb = g("rebase", "--onto", new, old, check=False, quiet=False)
        log(reb.stdout + reb.stderr)
    clean = not g.rebase_in_progress() and reb.returncode == 0
    res["rebase_output"] = tail(reb.stdout + reb.stderr, 6000)

    common = dict(repo=p["repo"], upstream=p["upstream"], old=old, new=new,
                  report_path=report_path, abort_path=abort_path, repo_dir=repo_dir)
    max_turns = int(fork.get("claude_max_turns", 200))

    if not clean:
        if not g.rebase_in_progress():
            return finish("failure", f"git rebase failed without stopping on a conflict:\n{reb.stderr[-2000:]}")
        conflict = conflict_state(g)
        res["conflicts"].append(conflict)
        task = CONFLICT_TASK.format(
            commit=conflict["commit"][:12], subject=conflict["subject"], new=new,
            files="\n".join(f"- `{f}`" for f in conflict["files"]) or "- (none listed)")
        info = run_claude(
            repo_dir=repo_dir, out_dir=out, mode="conflicts",
            system_prompt=render_prompt(**common, task_summary="The rebase stopped on a conflict.", task=task),
            user_prompt=f"Carry Semio's commits from semio/{old} onto {new}, resolving the rebase "
                        "conflicts as your instructions describe. Start by reading SEMIO.md.",
            max_turns=max_turns, timeout_minutes=claude_timeout_minutes, **_paths(report_path, abort_path))
        res["claude_runs"].append(info)
        ok, why = claude_outcome(g, new, abort_path, info)
        if not ok:
            if g.rebase_in_progress():
                later = conflict_state(g)
                if later["commit"] != conflict["commit"] and later["files"]:
                    res["conflicts"].append(later)
            abort_rebase(g)
            return finish("failure", why)

    # 3. Account for every Semio commit.
    res["commits"].update(account(g, old, new, originals))

    # 4. Checks, with one attempt by Claude to fix them.
    commands = fork.get("checks") or semio_md.check_commands(semio_text, fork.get("check_programs") or ["cargo"])
    res["check_commands"] = commands
    if not commands:
        return finish("failure", "no checks to run: SEMIO.md's \"Carrying\" section lists no check "
                                 "commands and forks.yml sets no `checks`")
    checks = run_checks(repo_dir, commands, out / "checks", check_timeout_minutes)
    res["checks"] = checks
    if any(c["status"] != "passed" for c in checks):
        failing = [c for c in checks if c["status"] != "passed"]
        before = g.rev("HEAD")
        task = FIX_TASK.format(
            new=new, how=" cleanly" if clean else " after you resolved its conflicts",
            failures="\n".join(f"- `{c['command']}` ({c['status']}, log `{c['log']}`)" for c in failing),
            logs_dir=out / "checks", abort_path=abort_path)
        info = run_claude(
            repo_dir=repo_dir, out_dir=out, mode="fix-checks",
            system_prompt=render_prompt(**common, task_summary="The rebase completed but checks fail.", task=task),
            user_prompt=f"The checks fail on carry/{new}. Fix them as your instructions describe. "
                        "Start by reading SEMIO.md and the failing logs.",
            max_turns=max_turns, timeout_minutes=claude_timeout_minutes, **_paths(report_path, abort_path))
        res["claude_runs"].append(info)
        ok, why = claude_outcome(g, new, abort_path, info)
        if not ok:
            abort_rebase(g)
            return finish("failure", "checks failed and Claude could not fix them: " + why)
        res["commits"].update(account(g, old, new, originals))
        res["fixed_from"] = before
        checks = run_checks(repo_dir, commands, out / "checks-after-fix", check_timeout_minutes)
        res["checks_before_fix"], res["checks"] = res["checks"], checks
        if any(c["status"] != "passed" for c in checks):
            return finish("failure", "checks still fail after Claude's attempt to fix them")

    # 5. Suggestions, and the bundle for the publish job.
    res["drop_suggestions"] = drop_suggestions(g, semio_text, new)
    res["semio_md_changed"] = (g.out("show", "HEAD:SEMIO.md", check=False) or "") != semio_text
    res["carry_sha"] = g.rev("HEAD")
    g("bundle", "create", str(out / "carry.bundle"), f"refs/heads/carry/{new}", f"^refs/tags/{new}")
    how = "cleanly" if clean else "with conflicts resolved by Claude"
    return finish("success", f"carried {len(res['commits'].get('carried', []))} commit(s) {how}; checks pass")


def _paths(report_path: Path, abort_path: Path) -> dict:
    return {"report_path": report_path, "abort_path": abort_path}


def commit_list(g: Git, rng: str) -> list[dict]:
    raw = g.out("log", "--reverse", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", rng)
    out = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, subject, body = (rec.split("\x1f") + ["", "", ""])[:4]
        if len(parents.split()) > 1:
            continue  # merge commits are not replayed
        m = CHERRY.search(body)
        out.append({"sha": sha, "subject": subject, "cherry_picked_from": m.group(1) if m else None})
    return out


def conflict_state(g: Git) -> dict:
    commit = g.out("rev-parse", "REBASE_HEAD", check=False)
    files = [f for f in g.out("diff", "--name-only", "--diff-filter=U").splitlines() if f]
    return {
        "commit": commit,
        "subject": g.out("log", "-1", "--format=%s", commit, check=False) if commit else "",
        "files": files,
        "diff": head(g.out("diff", check=False), 20000),
    }


def claude_outcome(g: Git, new: str, abort_path: Path, info: dict) -> tuple[bool, str]:
    """The script, not Claude, decides whether the carry succeeded: by the
    state Claude left, whatever Claude says."""
    if info.get("turns") is None and info.get("exit_code"):
        return False, ("Claude Code did not run (exit code " + str(info["exit_code"]) + "):\n```\n"
                       + info.get("stderr_tail", "")[-3000:] + "\n```")
    if abort_path.exists():
        return False, "Claude gave up and explained why in SYNC_ABORT.md"
    problem = repo_problem(g, new)
    if not problem:
        return True, ""
    if info.get("timed_out"):
        problem += f" (Claude Code hit its time limit after {info.get('seconds')} s)"
    elif info.get("is_error") or (info.get("subtype") or "").startswith("error"):
        problem += (f" (Claude Code stopped with {info.get('subtype')} after {info.get('turns')} turns: "
                    f"{info.get('final_message', '')[:1000]})")
    return False, problem


def repo_problem(g: Git, new: str) -> str:
    if g.rebase_in_progress():
        return "Claude exited with the rebase still in progress"
    branch = g.current_branch()
    if branch != f"carry/{new}":
        return f"Claude left {branch or 'a detached HEAD'} checked out instead of carry/{new}"
    status = g.status_porcelain()
    if status:
        return "Claude left the working tree dirty:\n" + head(status, 2000)
    if not g.is_ancestor(f"refs/tags/{new}", "HEAD"):
        return f"carry/{new} does not contain the tag {new}"
    if g.out("rev-list", "--merges", f"refs/tags/{new}..HEAD"):
        return f"carry/{new} contains merge commits"
    return ""


def abort_rebase(g: Git) -> None:
    if g.rebase_in_progress():
        g("rebase", "--abort", check=False)


def account(g: Git, old: str, new: str, originals: list[dict]) -> dict:
    """Match each original Semio commit to its carried counterpart, or prove
    that upstream already has it."""
    carried = commit_list(g, f"refs/tags/{new}..HEAD")
    by_subject: dict[str, list[dict]] = {}
    for c in carried:
        by_subject.setdefault(c["subject"], []).append(c)
    # git cherry marks with '-' the commits whose change <new> already has.
    upstream_equiv = set()
    for line in g.out("cherry", f"refs/tags/{new}", f"semio/{old}", f"refs/tags/{old}", check=False).splitlines():
        if line.startswith("- "):
            upstream_equiv.add(line[2:].strip())
    kept, dropped_upstream, dropped_unproven = [], [], []
    matched = set()
    for o in originals:
        cands = [c for c in by_subject.get(o["subject"], []) if c["sha"] not in matched]
        if cands:
            matched.add(cands[0]["sha"])
            kept.append({**o, "new_sha": cands[0]["sha"]})
            continue
        src = o.get("cherry_picked_from")
        if o["sha"] in upstream_equiv:
            dropped_upstream.append({**o, "proof": f"{new} contains an identical change (git cherry)"})
        elif src and g.has(src) and g.is_ancestor(src, f"refs/tags/{new}"):
            dropped_upstream.append({**o, "proof": f"cherry-picked from {src[:12]}, which {new} contains"})
        else:
            dropped_unproven.append(o)
    added = [c for c in carried if c["sha"] not in matched]
    return {"carried": kept, "dropped_upstream": dropped_upstream,
            "dropped_unproven": dropped_unproven, "added": added}


def install_rust_toolchain(g: Git, new: str) -> None:
    """Install the toolchain the new release pins, so that both the checks and
    Claude's cargo commands use it."""
    for name in ("rust-toolchain.toml", "rust-toolchain"):
        text = g.out("show", f"refs/tags/{new}:{name}", check=False)
        if text:
            break
    else:
        return
    m = re.search(r'channel\s*=\s*"([^"]+)"', text) or re.match(r"\s*(\S+)\s*$", text)
    if not m or not shutil.which("rustup"):
        return
    channel = m.group(1)
    comps = re.search(r"components\s*=\s*\[([^\]]*)\]", text)
    extra = re.findall(r'"([^"]+)"', comps.group(1)) if comps else []
    with group(f"rustup toolchain install {channel}"):
        run(["rustup", "toolchain", "install", channel, "--profile", "minimal", "--no-self-update",
             "-c", ",".join(sorted(set(["rustfmt", "clippy", *extra])))], capture=False, timeout=1800)


def run_checks(repo_dir: Path, commands: list[str], log_dir: Path, timeout_minutes: int) -> list[dict]:
    log_dir.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env(extra_drop=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"))
    env.setdefault("CARGO_TERM_COLOR", "never")
    results = []
    for i, cmd in enumerate(commands, 1):
        log_path = log_dir / f"{i:02d}.log"
        started = time.monotonic()
        with group(f"check {i}/{len(commands)}: {cmd}"), log_path.open("w", encoding="utf-8") as lf:
            lf.write(f"$ {cmd}\n")
            lf.flush()
            try:
                proc = subprocess.run(shlex.split(cmd), cwd=repo_dir, env=env, stdout=lf,
                                      stderr=subprocess.STDOUT, timeout=timeout_minutes * 60)
                status = "passed" if proc.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                status = "timed out"
            except FileNotFoundError as e:
                status = "failed"
                lf.write(str(e) + "\n")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        log(tail(text, 20000))
        results.append({
            "command": cmd, "status": status, "seconds": round(time.monotonic() - started),
            "log": str(log_path), "log_tail": tail(text, 6000) if status != "passed" else tail(text, 1500),
        })
        log(f"check {status}: {cmd}")
    return results


def drop_suggestions(g: Git, semio_text: str, new: str) -> list[dict]:
    out = []
    for cond in semio_md.drop_conditions(semio_text):
        satisfied = None
        evidence = []
        for sha in cond["commits"]:
            if g.has(sha):
                inside = g.is_ancestor(sha, f"refs/tags/{new}")
                evidence.append(f"{sha[:12]} is {'in' if inside else 'not in'} {new}")
                satisfied = inside if satisfied is None else (satisfied and inside)
        out.append({**cond, "looks_satisfied": satisfied, "evidence": evidence})
    return out
