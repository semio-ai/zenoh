"""Ordering release tags."""

from __future__ import annotations

import re


def key(version: str) -> tuple:
    """Sort key for a version string: numeric parts compare as numbers, and a
    version with a pre-release suffix sorts before the same version without."""
    main, _, pre = version.partition("-")
    nums = tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.+]", main))
    # (nums, 1) for releases, (nums, 0, pre) for pre-releases
    return (nums, 0, pre) if pre else (nums, 1, "")


def matching(tags: list[str], regex: str) -> list[str]:
    r = re.compile(regex)
    return sorted((t for t in tags if r.search(t)), key=key)


def newest(versions: list[str]) -> str | None:
    return max(versions, key=key) if versions else None
