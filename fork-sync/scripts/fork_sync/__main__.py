"""fork-sync command line. See README.md.

  python3 scripts/fork-sync matrix  [--repo R]
  python3 scripts/fork-sync plan    --repo R [--version V]
  python3 scripts/fork-sync carry   --repo R [--install-toolchain]
  python3 scripts/fork-sync publish --repo R
  python3 scripts/fork-sync run     --repo R [--version V] --dry-run   # all three, locally
  python3 scripts/fork-sync notify  --text T                            # Slack, if configured
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import config, forge as forge_mod
from .carry import carry
from .plan import plan, upstream_url_for
from .publish import notify, publish
from .util import SyncError, error, log, run_url, set_output, summary, write_json

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fork-sync", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["matrix", "plan", "carry", "publish", "run", "notify"])
    ap.add_argument("--repo", help="fork, owner/name or name (as in forks.yml)")
    ap.add_argument("--version", help="carry onto this upstream tag instead of the newest release")
    ap.add_argument("--config", default=str(ROOT / "forks.yml"))
    ap.add_argument("--work", help="working directory (default: ./work/<fork name>)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything locally; print what would be pushed or opened")
    ap.add_argument("--local-forge", help="use bare repositories under this directory instead of GitHub (tests)")
    ap.add_argument("--install-toolchain", action="store_true",
                    help="install the Rust toolchain the new release pins (CI)")
    ap.add_argument("--text", help="notify: the message to post to Slack")
    ap.add_argument("--claude-timeout-minutes", type=int, default=150)
    ap.add_argument("--check-timeout-minutes", type=int, default=90)
    args = ap.parse_args(argv)

    if args.command == "notify":
        notify(args.text or "")
        return 0
    forks = config.load(Path(args.config))
    if args.command == "matrix":
        chosen = [config.find(forks, args.repo)] if args.repo else forks
        matrix = [f.to_dict() for f in chosen]
        set_output("matrix", json.dumps(matrix))
        print(json.dumps(matrix, indent=2))
        return 0

    if not args.repo:
        ap.error("--repo is required")
    fork = config.find(forks, args.repo)
    if args.dry_run:
        os.environ["FORK_SYNC_DRY_RUN"] = "1"
    forge = forge_mod.from_args(args.local_forge, args.dry_run)
    work = Path(args.work or Path("work") / fork.name).resolve()
    version = (args.version or "").strip() or None
    url = run_url()

    try:
        if args.command in ("plan", "run"):
            if args.command == "run" and work.exists() and any(work.iterdir()):
                raise SyncError(f"{work} is not empty; remove it or pass another --work")
            p = plan(fork, forge, work, version)
            for k in ("action", "reason", "old", "new"):
                set_output(k, str(p.get(k) or ""))
            if p["action"] != "carry":
                summary(f"### {fork.repo}\n\nMirror: {p['mirror']['status']} — {p['mirror']['detail']}\n\n"
                        f"Nothing to carry: {p['reason']}")
            if args.command == "plan" or p["action"] != "carry":
                return 0
        if args.command in ("carry", "run"):
            try:
                carry(work, upstream_url_for(forge, fork), install_toolchain=args.install_toolchain,
                      claude_timeout_minutes=args.claude_timeout_minutes,
                      check_timeout_minutes=args.check_timeout_minutes)
            except Exception as e:  # leave a result the publish job can report
                write_json(work / "out" / "result.json", {
                    "status": "failure", "reason": f"the carry job hit an error: {e}",
                    "old": None, "new": None, "conflicts": [], "claude_runs": [], "checks": [], "commits": {}})
                raise
            if args.command == "carry":
                return 0
        if args.command in ("publish", "run"):
            return publish(fork, forge, work, url)
    except SyncError as e:
        error(str(e))
        summary(f"### {fork.repo}: {args.command} failed\n\n```\n{e}\n```")
        if args.command in ("plan",):
            notify(f"fork-sync: {args.command} failed for {fork.repo}: {str(e)[:300]} — {url}")
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
