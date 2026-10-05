"""Thin wrapper around the git command line."""

from __future__ import annotations

import os
from pathlib import Path

from .util import SyncError, run

# Keep git from opening editors, running repository hooks, or signing.
BASE_CONFIG = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "commit.gpgSign=false",
    "-c", "tag.gpgSign=false",
    "-c", "advice.detachedHead=false",
]


class Git:
    def __init__(self, path: Path, env: dict | None = None, bare: bool = False):
        self.path = Path(path)
        self.env = {"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}
        if env:
            self.env.update(env)
        self.bare = bare

    def __call__(self, *args: str, check: bool = True, quiet: bool = False, env: dict | None = None,
                 timeout: float | None = None):
        loc = ["--git-dir", str(self.path)] if self.bare else ["-C", str(self.path)]
        e = dict(self.env)
        if env:
            e.update(env)
        return run(["git", *loc, *BASE_CONFIG, *args], env=e, check=check, quiet=quiet, timeout=timeout)

    def out(self, *args: str, **kw) -> str:
        return self(*args, quiet=kw.pop("quiet", True), **kw).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self(*args, check=False, quiet=True).returncode == 0

    def rev(self, ref: str) -> str:
        return self.out("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")

    def has(self, ref: str) -> bool:
        return self.ok("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def git_dir(self) -> Path:
        d = Path(self.out("rev-parse", "--git-dir"))
        return d if d.is_absolute() else self.path / d

    def rebase_in_progress(self) -> bool:
        gd = self.git_dir()
        return (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()

    def current_branch(self) -> str | None:
        p = self(*["symbolic-ref", "--quiet", "--short", "HEAD"], check=False, quiet=True)
        return p.stdout.strip() if p.returncode == 0 else None

    def status_porcelain(self) -> str:
        return self.out("status", "--porcelain", "--untracked-files=normal")


def ls_remote(url: str, *patterns: str, env: dict | None = None) -> dict[str, str]:
    """ref name -> object id. Annotated tags are reported peeled (the commit)."""
    proc = run(["git", "ls-remote", url, *patterns], env={"GIT_TERMINAL_PROMPT": "0", **(env or {})},
               quiet=True, timeout=300)
    refs: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            refs[name] = sha
    refs.update(peeled)
    return refs


def tags_of(refs: dict[str, str]) -> dict[str, str]:
    return {k[len("refs/tags/"):]: v for k, v in refs.items() if k.startswith("refs/tags/")}


def heads_of(refs: dict[str, str]) -> dict[str, str]:
    return {k[len("refs/heads/"):]: v for k, v in refs.items() if k.startswith("refs/heads/")}


def init_bare(path: Path) -> Git:
    path = Path(path)
    if path.exists():
        raise SyncError(f"{path} already exists")
    run(["git", "init", "--quiet", "--bare", str(path)], quiet=True)
    return Git(path, bare=True)


def clean_env_for_git() -> dict:
    """git honours GIT_DIR and friends from the environment; make sure a stray
    one from the caller does not redirect our commands."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
