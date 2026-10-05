"""Phase 1 (has a GitHub App token, runs no third-party code): fast-forward the
fork's main, decide whether there is a release to carry onto, create the new
semio/<new> line at the upstream tag, and bundle semio/<old> for the carry job."""

from __future__ import annotations

import re
from pathlib import Path

from . import versions
from .config import Fork
from .forge import Forge, failure_issue_title
from .git import Git, heads_of, init_bare, ls_remote, tags_of
from .util import SyncError, log, warn, write_json

LINE = re.compile(r"^semio/(.+)$")


def plan(fork: Fork, forge: Forge, work: Path, version: str | None = None) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    upstream_url = upstream_url_for(forge, fork)
    fork_url = forge.repo_url(fork.repo)
    genv = forge.git_env()

    up_refs = ls_remote(upstream_url, "refs/heads/*", "refs/tags/*")
    fork_refs = ls_remote(fork_url, "refs/heads/*", env=genv)
    tags = tags_of(up_refs)
    up_heads = heads_of(up_refs)
    fork_heads = heads_of(fork_refs)

    p: dict = {
        "repo": fork.repo,
        "upstream": fork.upstream,
        "fork": fork.to_dict(),
        "action": "skip",
        "reason": "",
        "forced_version": version,
    }
    scratch = init_bare(work / "plan.git")
    scratch.env.update(genv)

    # 1. Mirror main.
    p["mirror"] = mirror(fork, forge, scratch, upstream_url, fork_url, up_heads, fork_heads)

    # 2. Detect work.
    release_tags = [t for t in versions.matching(list(tags), fork.tag_regex) if t not in fork.skip_versions]
    lines = {m.group(1): sha for b, sha in fork_heads.items() if (m := LINE.match(b))}
    # A line carries Semio's commits when its tip is not the bare upstream tag.
    # A line created by an earlier run and not merged into yet is still bare.
    carried = sorted((v for v, sha in lines.items() if tags.get(v) != sha), key=versions.key)
    p["lines"] = {v: {"sha": sha, "carried": v in carried} for v, sha in lines.items()}

    if version:
        if version not in tags:
            raise SyncError(f"{version} is not a tag of {fork.upstream}")
        new = version
    else:
        new = versions.newest(release_tags)
        if new is None:
            return _done(p, work, "skip", f"no upstream tag matches {fork.tag_regex}")
        if carried and versions.key(carried[-1]) >= versions.key(new):
            return _done(p, work, "skip", f"up to date: semio/{carried[-1]} is the newest line, "
                                          f"upstream's newest release is {new}")
    if new in carried:
        return _done(p, work, "skip", f"semio/{new} already carries Semio's commits")
    older = [v for v in carried if versions.key(v) < versions.key(new)]
    if not older:
        return _done(p, work, "skip", f"no semio/* line older than {new} carries Semio's commits")
    old = older[-1]
    p.update(old=old, new=new, new_sha=tags[new], old_sha=tags.get(old), semio_old_sha=lines[old])
    if not tags.get(old):
        raise SyncError(f"semio/{old} has no matching upstream tag {old}")

    # Idempotency: an open pull request or failure issue means a human has it.
    prs = forge.open_prs(fork.repo, f"carry/{new}", f"semio/{new}")
    if prs:
        return _done(p, work, "skip", f"carry PR already open: {prs[0]['url']}")
    issue = forge.find_open_issue(fork.repo, failure_issue_title(new))
    if issue and not version:
        return _done(p, work, "skip", f"failure issue already open: {issue['url']} "
                                      "(close it, or run the workflow with version set, to retry)")
    p["open_issue"] = issue
    p["carry_branch_sha"] = fork_heads.get(f"carry/{new}")

    # 3. Create the line at the tag, unchanged. Never moves an existing branch.
    existing = fork_heads.get(f"semio/{new}")
    if existing is None:
        log(f"creating semio/{new} at {new} ({tags[new]})")
        if not forge.create_branch(fork.repo, f"semio/{new}", tags[new]):
            scratch("fetch", "--no-tags", upstream_url, f"refs/tags/{new}:refs/tags/{new}")
            push(forge, scratch, fork_url, f"{tags[new]}:refs/heads/semio/{new}", force=False)
        p["line_created"] = True
    elif existing != tags[new]:
        raise SyncError(f"semio/{new} exists at {existing}, not at the tag {new} ({tags[new]})")
    else:
        p["line_created"] = False

    # 4. Bundle semio/<old> on top of the tag <old> for the carry job, which
    #    clones the public upstream and holds no GitHub credentials.
    scratch("fetch", "--no-tags", upstream_url, f"refs/tags/{old}:refs/tags/{old}")
    scratch("fetch", "--no-tags", fork_url, f"refs/heads/semio/{old}:refs/heads/semio/{old}")
    if scratch.rev(f"refs/heads/semio/{old}") != lines[old]:
        raise SyncError(f"semio/{old} moved while planning; run again")
    if not scratch.is_ancestor(f"refs/tags/{old}", f"refs/heads/semio/{old}"):
        raise SyncError(f"semio/{old} does not start at the tag {old}")
    bundle = work / "input.bundle"
    scratch("bundle", "create", str(bundle), f"refs/heads/semio/{old}", f"^refs/tags/{old}")

    name, email = forge.bot_identity()
    p["bot"] = {"name": name, "email": email}
    return _done(p, work, "carry", f"carry semio/{old} onto {new}")


def mirror(fork: Fork, forge: Forge, scratch: Git, upstream_url: str, fork_url: str,
           up_heads: dict, fork_heads: dict) -> dict:
    branch = fork.mirror_branch
    up, mine = up_heads.get(branch), fork_heads.get(branch)
    if up is None or mine is None:
        warn(f"mirror: {branch} missing upstream or in the fork; not touched")
        return {"status": "missing", "detail": f"{branch} missing"}
    if up == mine:
        return {"status": "up-to-date", "detail": f"{branch} is at upstream {up[:12]}"}
    scratch("fetch", "--no-tags", upstream_url, f"refs/heads/{branch}:refs/upstream/{branch}")
    scratch("fetch", "--no-tags", fork_url, f"refs/heads/{branch}:refs/fork/{branch}")
    if not scratch.is_ancestor(f"refs/fork/{branch}", f"refs/upstream/{branch}"):
        msg = (f"{fork.repo}:{branch} has diverged from {fork.upstream}:{branch} "
               "(it has commits upstream does not); not touched. Mirror it by hand.")
        warn("mirror: " + msg)
        return {"status": "diverged", "detail": msg}
    behind = scratch.out("rev-list", "--count", f"refs/fork/{branch}..refs/upstream/{branch}")
    res = forge.merge_upstream(fork.repo, branch)
    if forge.dry_run:
        return {"status": "would fast-forward", "detail": f"{branch} is {behind} commit(s) behind upstream {up[:12]}"}
    if res.get("ok") and res.get("merge_type") in ("fast-forward", "none"):
        return {"status": "fast-forwarded", "detail": f"{branch} fast-forwarded by {behind} commit(s) to {up[:12]}"}
    if res.get("ok"):
        warn(f"mirror: merge-upstream answered merge_type={res.get('merge_type')}")
        return {"status": "unexpected", "detail": str(res)}
    # Not a GitHub fork, or the API refused: push the fast-forward with git.
    log(f"merge-upstream failed ({res.get('message')}); pushing the fast-forward with git")
    try:
        push(forge, scratch, fork_url, f"refs/upstream/{branch}:refs/heads/{branch}", force=False)
    except SyncError as e:
        warn(f"mirror: could not fast-forward {branch}: {e}")
        return {"status": "failed", "detail": str(e)[:500]}
    return {"status": "fast-forwarded", "detail": f"{branch} fast-forwarded by {behind} commit(s) (git push)"}


def push(forge: Forge, repo: Git, url: str, refspec: str, force: bool, lease: str | None = None) -> None:
    if force and not lease:
        raise SyncError("refusing to force-push without a lease")
    args = ["push", "--no-verify", url, refspec]
    if lease:
        args.insert(1, f"--force-with-lease={lease}")
    if forge.dry_run:
        log(f"[dry-run] WOULD git {' '.join(args)}")
        return
    repo(*args)


def upstream_url_for(forge: Forge, fork: Fork) -> str:
    return forge.repo_url(fork.upstream)


def _done(p: dict, work: Path, action: str, reason: str) -> dict:
    p["action"], p["reason"] = action, reason
    log(f"plan: {action}: {reason}")
    write_json(work / "plan.json", p)
    return p
