"""Where the forks live: GitHub, or a local directory of bare repositories used
by the tests. Writes go through one small interface so that --dry-run can print
them instead."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import SyncError, log, mask, run


def failure_issue_title(version: str) -> str:
    return f"fork-sync: carrying onto {version} failed"


class Forge:
    """Interface. `repo` arguments are owner/name."""

    dry_run = False

    def repo_url(self, repo: str) -> str:
        raise NotImplementedError

    def git_env(self) -> dict:
        """Environment that authenticates git commands against the forge."""
        return {}

    def merge_upstream(self, repo: str, branch: str) -> dict:
        raise NotImplementedError

    def create_branch(self, repo: str, branch: str, sha: str) -> bool:
        """Create refs/heads/<branch> at sha. Returns False when the forge
        cannot do it by API (the caller then pushes with git)."""
        raise NotImplementedError

    def open_prs(self, repo: str, head: str, base: str) -> list[dict]:
        raise NotImplementedError

    def find_open_issue(self, repo: str, title: str) -> dict | None:
        raise NotImplementedError

    def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        raise NotImplementedError

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        raise NotImplementedError

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        raise NotImplementedError

    def update_issue(self, repo: str, number: int, body: str) -> None:
        raise NotImplementedError

    def comment(self, repo: str, number: int, body: str) -> None:
        raise NotImplementedError

    def close_issue(self, repo: str, number: int) -> None:
        raise NotImplementedError

    def bot_identity(self) -> tuple[str, str]:
        raise NotImplementedError


class GitHub(Forge):
    api = "https://api.github.com"

    def __init__(self, token: str | None, app_slug: str = "semio-fork-sync"):
        self.token = token or None
        self.app_slug = app_slug
        if self.token:
            mask(self.token)

    # -- plumbing ---------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None, ok=(200, 201)):
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "semio-fork-sync")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                status = resp.status
                link = resp.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            raw = e.read()
            status = e.code
            link = ""
        payload = json.loads(raw) if raw else None
        if status not in ok:
            msg = payload.get("message") if isinstance(payload, dict) else raw[:500]
            raise GitHubError(status, f"{method} {path}: {status} {msg}")
        return payload, link

    def _get_all(self, path: str) -> list:
        items: list = []
        url = path + ("&" if "?" in path else "?") + "per_page=100"
        while url:
            page, link = self._request("GET", url)
            items.extend(page)
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part[part.find("<") + 1 : part.find(">")]
        return items

    # -- interface --------------------------------------------------------
    def repo_url(self, repo: str) -> str:
        return f"https://github.com/{repo}.git"

    def git_env(self) -> dict:
        if not self.token:
            return {}
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        mask(basic)
        # Passed through the environment so that it is neither written to a
        # .git/config nor visible in the command line.
        return {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def merge_upstream(self, repo: str, branch: str) -> dict:
        try:
            payload, _ = self._request("POST", f"/repos/{repo}/merge-upstream", {"branch": branch})
            return {"ok": True, "merge_type": payload.get("merge_type"), "message": payload.get("message")}
        except GitHubError as e:
            return {"ok": False, "status": e.status, "message": str(e)}

    def create_branch(self, repo: str, branch: str, sha: str) -> bool:
        try:
            self._request("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
            return True
        except GitHubError as e:
            if e.status == 422 and "already exists" in str(e):
                raise SyncError(f"{branch} already exists in {repo}") from e
            if e.status in (403, 404, 422):
                log(f"creating {branch} by API failed ({e}); falling back to git push")
                return False
            raise

    def open_prs(self, repo: str, head: str, base: str) -> list[dict]:
        owner = repo.split("/")[0]
        q = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{head}", "base": base})
        prs = self._get_all(f"/repos/{repo}/pulls?{q}")
        return [{"number": p["number"], "url": p["html_url"]} for p in prs]

    def find_open_issue(self, repo: str, title: str) -> dict | None:
        for it in self._get_all(f"/repos/{repo}/issues?state=open"):
            if "pull_request" not in it and it["title"] == title:
                return {"number": it["number"], "url": it["html_url"]}
        return None

    def create_pr(self, repo, head, base, title, body):
        p, _ = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body, "maintainer_can_modify": False},
        )
        return {"number": p["number"], "url": p["html_url"]}

    def add_labels(self, repo, number, labels):
        if labels:
            self._request("POST", f"/repos/{repo}/issues/{number}/labels", {"labels": labels})

    def create_issue(self, repo, title, body, labels):
        try:
            p, _ = self._request(
                "POST", f"/repos/{repo}/issues", {"title": title, "body": body, "labels": labels}
            )
        except GitHubError as e:
            if e.status == 410:
                raise SyncError(f"issues are disabled on {repo}; enable them (see SETUP.md)") from e
            raise
        return {"number": p["number"], "url": p["html_url"]}

    def update_issue(self, repo, number, body):
        self._request("PATCH", f"/repos/{repo}/issues/{number}", {"body": body})

    def comment(self, repo, number, body):
        self._request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})

    def close_issue(self, repo, number):
        self._request(
            "PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed", "state_reason": "completed"}
        )

    def bot_identity(self) -> tuple[str, str]:
        login = f"{self.app_slug}[bot]"
        try:
            user, _ = self._request("GET", f"/users/{urllib.parse.quote(login)}")
            return login, f"{user['id']}+{login}@users.noreply.github.com"
        except GitHubError:
            return login, f"{login}@users.noreply.github.com"


class GitHubError(SyncError):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


class Local(Forge):
    """Forks and upstreams as bare repositories under `root/<owner>/<name>.git`,
    pull requests and issues in `root/state.json`. Used by tests/."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state_path = self.root / "state.json"

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"prs": [], "issues": [], "next": 1}

    def _save(self, st: dict) -> None:
        self.state_path.write_text(json.dumps(st, indent=2) + "\n")

    def _new(self, st: dict, kind: str, repo: str, **fields) -> dict:
        n = st["next"]
        st["next"] += 1
        item = {"number": n, "repo": repo, "state": "open", "url": f"local://{repo}/{kind}/{n}",
                "author": self.bot_identity()[0], "comments": [], **fields}
        st[kind].append(item)
        self._save(st)
        return {"number": n, "url": item["url"]}

    def repo_url(self, repo: str) -> str:
        return str(self.root / f"{repo}.git")

    def merge_upstream(self, repo: str, branch: str) -> dict:
        upstream = self._upstream_of(repo)
        proc = run(
            ["git", "--git-dir", self.repo_url(repo), "fetch", self.repo_url(upstream), f"{branch}:{branch}"],
            check=False,
        )
        if proc.returncode != 0:
            return {"ok": False, "status": 409, "message": proc.stderr.strip()}
        return {"ok": True, "merge_type": "fast-forward", "message": "fast-forwarded"}

    def _upstream_of(self, repo: str) -> str:
        meta = json.loads((self.root / "upstreams.json").read_text())
        return meta[repo]

    def create_branch(self, repo: str, branch: str, sha: str) -> bool:
        return False  # always pushed with git, like a GitHub fork without shared objects

    def open_prs(self, repo, head, base):
        return [{"number": p["number"], "url": p["url"]} for p in self._state()["prs"]
                if p["repo"] == repo and p["head"] == head and p["base"] == base and p["state"] == "open"]

    def find_open_issue(self, repo, title):
        for i in self._state()["issues"]:
            if i["repo"] == repo and i["title"] == title and i["state"] == "open":
                return {"number": i["number"], "url": i["url"]}
        return None

    def create_pr(self, repo, head, base, title, body):
        return self._new(self._state(), "prs", repo, head=head, base=base, title=title, body=body, labels=[])

    def _find(self, st, number):
        for kind in ("prs", "issues"):
            for it in st[kind]:
                if it["number"] == number:
                    return it
        raise SyncError(f"no item #{number}")

    def add_labels(self, repo, number, labels):
        st = self._state()
        self._find(st, number)["labels"] = sorted(set(self._find(st, number).get("labels", []) + labels))
        self._save(st)

    def create_issue(self, repo, title, body, labels):
        return self._new(self._state(), "issues", repo, title=title, body=body, labels=labels)

    def update_issue(self, repo, number, body):
        st = self._state()
        self._find(st, number)["body"] = body
        self._save(st)

    def comment(self, repo, number, body):
        st = self._state()
        self._find(st, number)["comments"].append(body)
        self._save(st)

    def close_issue(self, repo, number):
        st = self._state()
        self._find(st, number)["state"] = "closed"
        self._save(st)

    def bot_identity(self):
        return "semio-fork-sync[bot]", "1+semio-fork-sync[bot]@users.noreply.github.com"


class DryRun(Forge):
    """Reads from the wrapped forge, prints writes."""

    dry_run = True

    def __init__(self, inner: Forge):
        self.inner = inner
        self._n = 900000

    def _would(self, what: str, body: str | None = None) -> dict:
        self._n += 1
        log(f"[dry-run] WOULD {what}")
        if body:
            log("[dry-run] ---- body ----\n" + body + "\n[dry-run] ---- end body ----")
        return {"number": self._n, "url": f"(dry-run #{self._n})"}

    def repo_url(self, repo):
        return self.inner.repo_url(repo)

    def git_env(self):
        return self.inner.git_env()

    def merge_upstream(self, repo, branch):
        self._would(f"fast-forward {repo}:{branch} to upstream (merge-upstream API)")
        return {"ok": True, "merge_type": "fast-forward", "message": "dry run"}

    def create_branch(self, repo, branch, sha):
        self._would(f"create branch {branch} at {sha} in {repo}")
        return True

    def open_prs(self, repo, head, base):
        return self.inner.open_prs(repo, head, base)

    def find_open_issue(self, repo, title):
        return self.inner.find_open_issue(repo, title)

    def create_pr(self, repo, head, base, title, body):
        return self._would(f"open PR in {repo}: {head} -> {base}, title {title!r}", body)

    def add_labels(self, repo, number, labels):
        self._would(f"label #{number} in {repo} with {labels}")

    def create_issue(self, repo, title, body, labels):
        return self._would(f"open issue in {repo}: {title!r} labels={labels}", body)

    def update_issue(self, repo, number, body):
        self._would(f"replace the body of issue #{number} in {repo}", body)

    def comment(self, repo, number, body):
        self._would(f"comment on #{number} in {repo}", body)

    def close_issue(self, repo, number):
        self._would(f"close issue #{number} in {repo}")

    def bot_identity(self):
        return self.inner.bot_identity()


def from_args(local_root: str | None, dry_run: bool) -> Forge:
    if local_root:
        forge: Forge = Local(Path(local_root))
    else:
        forge = GitHub(os.environ.get("FORK_SYNC_TOKEN"),
                       os.environ.get("FORK_SYNC_APP_SLUG", "semio-fork-sync"))
    return DryRun(forge) if dry_run else forge
