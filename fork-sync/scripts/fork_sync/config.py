"""Loading forks.yml."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .util import SyncError

BUILTIN_DEFAULTS = {
    "tag_regex": r"^\d+\.\d+\.\d+$",
    "labels": ["enhancement"],
    "issue_labels": [],
    "checks": None,
    "check_programs": ["cargo"],
    "timeout_minutes": 240,
    "claude_max_turns": 200,
    "free_disk": True,
    "skip_versions": [],
    "mirror_branch": "main",
}


@dataclass
class Fork:
    repo: str
    upstream: str
    tag_regex: str
    labels: list[str]
    issue_labels: list[str]
    checks: list[str] | None
    check_programs: list[str]
    timeout_minutes: int
    claude_max_turns: int
    free_disk: bool
    skip_versions: list[str]
    mirror_branch: str
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.repo.split("/", 1)[1]

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in BUILTIN_DEFAULTS}
        d.update(repo=self.repo, upstream=self.upstream, name=self.name)
        return d


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        pass
    # GitHub's Ubuntu runners ship mikefarah's yq; use it when PyYAML is absent.
    if shutil.which("yq"):
        out = subprocess.run(
            ["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True
        ).stdout
        return json.loads(out) or {}
    raise SyncError("reading forks.yml needs PyYAML or yq")


def load(path: Path) -> list[Fork]:
    data = _load_yaml(Path(path))
    defaults = dict(BUILTIN_DEFAULTS)
    defaults.update(data.get("defaults") or {})
    forks = []
    seen = set()
    for entry in data.get("forks") or []:
        merged = dict(defaults)
        merged.update(entry)
        for key in ("repo", "upstream"):
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(merged.get(key, ""))):
                raise SyncError(f"forks.yml: bad or missing `{key}` in {entry!r}")
        if merged["repo"] in seen:
            raise SyncError(f"forks.yml: {merged['repo']} is listed twice")
        seen.add(merged["repo"])
        re.compile(merged["tag_regex"])
        known = set(BUILTIN_DEFAULTS) | {"repo", "upstream"}
        forks.append(
            Fork(
                repo=merged["repo"],
                upstream=merged["upstream"],
                tag_regex=merged["tag_regex"],
                labels=list(merged["labels"] or []),
                issue_labels=list(merged["issue_labels"] or []),
                checks=list(merged["checks"]) if merged.get("checks") else None,
                check_programs=list(merged["check_programs"] or []),
                timeout_minutes=int(merged["timeout_minutes"]),
                claude_max_turns=int(merged["claude_max_turns"]),
                free_disk=bool(merged["free_disk"]),
                skip_versions=[str(v) for v in merged["skip_versions"] or []],
                mirror_branch=str(merged["mirror_branch"]),
                extra={k: v for k, v in merged.items() if k not in known},
            )
        )
    return forks


def find(forks: list[Fork], repo: str) -> Fork:
    for f in forks:
        if f.repo == repo or f.name == repo:
            return f
    raise SyncError(f"{repo} is not in forks.yml (known: {', '.join(f.repo for f in forks)})")
