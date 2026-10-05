Carries Semio's commits from `semio/1.10.1` onto the upstream release `1.10.2` ([eclipse-zenoh/zenoh@1.10.2](https://github.com/eclipse-zenoh/zenoh/releases/tag/1.10.2)).

Opened by fork-sync ((local run)). Merging is a human decision; the automation never merges.

## Commits

Carried:
- `3c87ad602121` → `4909814a0c40` feat(config): enforce access_control changes on a running node
- `377eb432eb4e` → `bf5fa12f9b13` docs: describe Semio's commits on this line in SEMIO.md
- `502e83de8fd9` → `6876b2522ea9` fix(config): give Notifier::lock() a read-only guard
- `6bfb368317ca` → `bd47991ccc26` chore(config): allow clippy::redundant_field_names in zenoh-config
- `e7a0588fd111` → `7357e2df8a61` ci: upload to Codecov only from eclipse-zenoh/zenoh
- `3d46433eacb8` → `3a24f56ebe5a` docs: list the CI maintenance commits in SEMIO.md

Dropped because upstream already contains them:
- `5d25cd0a3c06` With Rust 1.75 fix dependency to static_init 1.0.3 (#2782) — 1.10.2 contains an identical change (git cherry)
- `b7968c967b0d` chore: pin mio-serial for Rust 1.75 compatibility on Windows (#2785) — 1.10.2 contains an identical change (git cherry)
- `5d66130d2d67` chore: pin hyper-util and thiserror for Rust 1.75 compatibility (#2823) — 1.10.2 contains an identical change (git cherry)

## Conflicts

None: `git rebase` applied every commit cleanly.

## Checks

| Result | Command | Time |
| --- | --- | --- |
| ✅ passed | `cargo metadata --no-deps --format-version 1 --offline` | 0 s |

The repository's CI, TypeScript tests included where there are any, runs on this pull request.

## SEMIO.md "drop once …" conditions (suggestions only)

- Dependency pins for Rust 1.75: **looks satisfied** (8f226b5d6 is in 1.10.2). _Drop them once the line is rebased on a release that includes upstream 8f226b5d6; `git rebase` then skips them by itself._
- `#![allow(clippy::redundant_field_names)]` in `commons/zenoh-config/src/lib.rs`: needs a human check. _Drop it once the line is rebased on a release whose zenoh-config passes the stable clippy of the time without it._

The automation never applies these removals.

## Run

Workflow run: (local run)

Claude Code was not needed.
