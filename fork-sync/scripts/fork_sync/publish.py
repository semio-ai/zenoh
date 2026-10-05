"""Phase 3 (fresh runner, GitHub App token, runs no third-party code): check
what the carry job produced, then push carry/<new> and open the pull request,
or open/update the failure issue and push nothing."""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from pathlib import Path

from . import render
from .forge import Forge, failure_issue_title
from .git import Git, heads_of, init_bare, ls_remote
from .plan import push, upstream_url_for
from .config import Fork
from .util import SyncError, log, read_json, summary, warn, write_json


def publish(fork: Fork, forge: Forge, work: Path, run_url: str) -> int:
    plan = read_json(work / "plan.json")
    mirror_line = f"Mirror: {plan.get('mirror', {}).get('status')} — {plan.get('mirror', {}).get('detail')}"
    if plan["action"] != "carry":
        summary(f"### {plan['repo']}\n\n{mirror_line}\n\nNothing to carry: {plan['reason']}")
        return 0
    result_path = work / "out" / "result.json"
    res = read_json(result_path) if result_path.exists() else None

    if res is None:
        return failure(fork, forge, plan, None, "the carry job produced no result: it crashed, was cancelled, "
                       "or hit its timeout (see the run's log)", run_url, mirror_line)
    if res["status"] != "success":
        return failure(fork, forge, plan, res, res["reason"], run_url, mirror_line)
    try:
        sha = verify_and_push(fork, forge, work, plan, res)
    except SyncError as e:
        return failure(fork, forge, plan, res, f"the carried branch was not published: {e}", run_url, mirror_line)

    new = plan["new"]
    body = render.pr_body(plan, res, run_url)
    pr = forge.create_pr(fork.repo, f"carry/{new}", f"semio/{new}", render.pr_title(res), body)
    forge.add_labels(fork.repo, pr["number"], fork.labels)
    issue = forge.find_open_issue(fork.repo, failure_issue_title(new))
    if issue:
        forge.comment(fork.repo, issue["number"], f"Superseded by {pr['url']} (run {run_url}).")
        forge.close_issue(fork.repo, issue["number"])
    write_json(work / "published.json", {"pr": pr, "carry_sha": sha})
    flags = render.review_flags(res)
    summary(f"### {plan['repo']}: pull request opened\n\n{mirror_line}\n\n"
            f"Carried `semio/{plan['old']}` onto `{new}`: {pr['url']}\n\n{res['reason']}"
            + ("\n\nNeeds human review:\n" + "\n".join(f"- {f}" for f in flags) if flags else ""))
    notify(f"fork-sync: {plan['repo']} carried onto {new}, PR ready for review: {pr['url']}")
    return 0


def verify_and_push(fork: Fork, forge: Forge, work: Path, plan: dict, res: dict) -> str:
    """Re-check, from scratch and without trusting the carry job, that the
    bundle holds Semio's commits on top of the upstream tag, then push it."""
    old, new = plan["old"], plan["new"]
    branch = f"carry/{new}"
    bundle = work / "out" / "carry.bundle"
    if not bundle.exists():
        raise SyncError("carry.bundle is missing")
    pub_dir = work / "publish.git"
    if pub_dir.exists():
        shutil.rmtree(pub_dir)
    g = init_bare(pub_dir)
    g.env.update(forge.git_env())
    upstream_url, fork_url = upstream_url_for(forge, fork), forge.repo_url(fork.repo)

    up_tags = ls_remote(upstream_url, *(f"refs/tags/{t}{s}" for t in (new, old) for s in ("", "^{}")))
    if up_tags.get(f"refs/tags/{new}") != plan["new_sha"] or up_tags.get(f"refs/tags/{old}") != plan["old_sha"]:
        raise SyncError("the upstream tags moved since planning")
    g("fetch", "--quiet", "--no-tags", upstream_url, f"refs/tags/{new}:refs/tags/{new}",
      f"refs/tags/{old}:refs/tags/{old}")
    g("fetch", "--quiet", "--no-tags", fork_url, f"refs/heads/semio/{old}:refs/heads/semio/{old}")
    fork_heads = heads_of(ls_remote(fork_url, f"refs/heads/semio/{new}", f"refs/heads/{branch}", env=forge.git_env()))
    line = fork_heads.get(f"semio/{new}")
    if line != plan["new_sha"] and not (forge.dry_run and line is None):
        raise SyncError(f"semio/{new} is at {line}, not at the tag {new}")

    g("bundle", "verify", str(bundle), quiet=True)
    g("fetch", "--quiet", str(bundle), f"refs/heads/{branch}:refs/heads/{branch}")
    sha = g.rev(f"refs/heads/{branch}")
    if sha != res.get("carry_sha"):
        raise SyncError("the bundle does not hold the commit result.json names")
    if not g.is_ancestor(f"refs/tags/{new}", sha):
        raise SyncError(f"{branch} is not based on the tag {new}")
    if g.out("rev-list", "--merges", f"refs/tags/{new}..{sha}"):
        raise SyncError(f"{branch} contains merge commits")
    count = int(g.out("rev-list", "--count", f"refs/tags/{new}..{sha}"))
    if count == 0:
        raise SyncError(f"{branch} carries no commit")
    guard_workflows(g, old, new, sha)

    # Never force-push except our own carry/* branch, and only with a lease.
    lease = None
    existing = fork_heads.get(branch)
    if existing == sha:
        log(f"{branch} is already at {sha[:12]}")
        return sha
    if existing:
        if forge.open_prs(fork.repo, branch, f"semio/{new}"):
            raise SyncError(f"{branch} has an open pull request; not overwriting it")
        g("fetch", "--quiet", "--no-tags", fork_url, f"refs/heads/{branch}:refs/remotes/fork/{branch}")
        committer = g.out("log", "-1", "--format=%ce", f"refs/remotes/fork/{branch}")
        bot_email = plan["bot"]["email"]
        if committer != bot_email:
            raise SyncError(f"{branch} already exists and its tip was committed by {committer}, not by "
                            "fork-sync; a human owns it. Delete it to let fork-sync retry.")
        lease = f"refs/heads/{branch}:{existing}"
        log(f"replacing fork-sync's earlier {branch} ({existing[:12]}), which has no open pull request")
    push(forge, g, fork_url, f"{sha}:refs/heads/{branch}", force=bool(lease), lease=lease)
    return sha


def guard_workflows(g: Git, old: str, new: str, sha: str) -> None:
    """The carry may change .github/ only the way semio/<old> does: a pull
    request runs its workflows with the fork's secrets, so a workflow change
    that Semio did not review must not reach one."""
    def changes(a: str, b: str) -> str:
        diff = g.out("diff", "-U0", "--no-color", "--no-ext-diff", a, b, "--", ".github", quiet=True)
        return "\n".join(l for l in diff.splitlines() if not l.startswith(("index ", "@@")))

    before = changes(f"refs/tags/{old}", f"refs/heads/semio/{old}")
    after = changes(f"refs/tags/{new}", sha)
    if before != after:
        raise SyncError(
            "the carried branch changes .github/ differently from semio/" + old + ". A pull request would "
            "run those workflow changes with the fork's secrets, so a human must review them and push the "
            "branch (its bundle is in the run's artifacts).\n\nsemio/" + old + ":\n```diff\n" + before[:3000]
            + "\n```\ncarried:\n```diff\n" + after[:3000] + "\n```")


def failure(fork: Fork, forge: Forge, plan: dict, res: dict | None, reason: str, run_url: str,
            mirror_line: str) -> int:
    new = plan["new"]
    title = failure_issue_title(new)
    body = render.issue_body(plan, res, reason, run_url)
    existing = forge.find_open_issue(fork.repo, title)
    if existing:
        forge.update_issue(fork.repo, existing["number"], body)
        forge.comment(fork.repo, existing["number"], f"Failed again in {run_url}: {reason[:1000]}")
        issue = existing
    else:
        issue = forge.create_issue(fork.repo, title, body, fork.issue_labels)
    summary(f"### {plan['repo']}: carrying onto {new} failed\n\n{mirror_line}\n\n{reason}\n\n"
            f"Issue: {issue['url']}. Nothing was pushed.")
    notify(f"fork-sync: carrying {plan['repo']} onto {new} failed: {reason[:300]} — {issue['url']}")
    return 1


def notify(text: str) -> None:
    """Post to Slack when FORK_SYNC_SLACK_WEBHOOK is set; never fails the run."""
    url = os.environ.get("FORK_SYNC_SLACK_WEBHOOK")
    if not url:
        return
    if os.environ.get("FORK_SYNC_DRY_RUN") == "1":
        log(f"[dry-run] WOULD post to Slack: {text}")
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # noqa: BLE001 - notification is best effort
        warn(f"Slack notification failed: {type(e).__name__}")
