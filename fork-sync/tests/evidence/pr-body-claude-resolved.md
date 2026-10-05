Carries Semio's commits from `semio/1.10.1` onto the upstream release `1.11.0` ([eclipse-zenoh/zenoh@1.11.0](https://github.com/eclipse-zenoh/zenoh/releases/tag/1.11.0)).

Opened by fork-sync ((local run)). Merging is a human decision; the automation never merges.

> [!WARNING]
> **Needs human review**
> - Semio commit `6bfb368317ca` (chore(config): allow clippy::redundant_field_names in zenoh-config) is not on the carried branch and the automation could not prove upstream already has it.
> - `SEMIO.md` was changed during the carry; check that it still describes the line.
> - Claude Code changed code during this carry: read its report below, starting with its "Needs human review" section.

## Commits

Carried:
- `3c87ad602121` → `c23df01efb64` feat(config): enforce access_control changes on a running node
- `377eb432eb4e` → `2ac869971bc3` docs: describe Semio's commits on this line in SEMIO.md
- `502e83de8fd9` → `f7578795952b` fix(config): give Notifier::lock() a read-only guard
- `e7a0588fd111` → `69f147a601b9` ci: upload to Codecov only from eclipse-zenoh/zenoh
- `3d46433eacb8` → `f16a89d90316` docs: list the CI maintenance commits in SEMIO.md

Dropped because upstream already contains them:
- `5d25cd0a3c06` With Rust 1.75 fix dependency to static_init 1.0.3 (#2782) — 1.11.0 contains an identical change (git cherry)
- `b7968c967b0d` chore: pin mio-serial for Rust 1.75 compatibility on Windows (#2785) — 1.11.0 contains an identical change (git cherry)
- `5d66130d2d67` chore: pin hyper-util and thiserror for Rust 1.75 compatibility (#2823) — 1.11.0 contains an identical change (git cherry)

**Missing, not proven to be upstream:**
- `6bfb368317ca` chore(config): allow clippy::redundant_field_names in zenoh-config

## Conflicts

The rebase first stopped on `6bfb368317ca` (chore(config): allow clippy::redundant_field_names in zenoh-config), in `commons/zenoh-config/src/lib.rs`. Claude Code resolved it and any later conflicts. Its report (`SYNC_REPORT.md`):

<details open><summary>Claude's report</summary>

# Carry report: semio/1.10.1 → 1.11.0

Result: the rebase completed. `carry/1.11.0` is checked out and holds five Semio commits on top
of upstream tag `1.11.0` (74051d0cc). No rebase is in progress and no tracked file is modified.

```
f16a89d90 docs: list the CI maintenance commits in SEMIO.md      (SEMIO.md updated, see below)
69f147a60 ci: upload to Codecov only from eclipse-zenoh/zenoh
f75787959 fix(config): give Notifier::lock() a read-only guard
2ac869971 docs: describe Semio's commits on this line in SEMIO.md
c23df01ef feat(config): enforce access_control changes on a running node
```

`git range-diff 1.10.1..semio/1.10.1 1.11.0..carry/1.11.0` shows every replayed commit equal
(`=`) to its original, apart from the SEMIO.md update folded into the last one.

## Needs human review

- **Untracked `.mcp.json` at the repository root.** It is a character device node (major 1,
  minor 3, that is `/dev/null`), root-owned, visible both inside and outside the command
  sandbox. It is not part of the repository and I did not create it; it looks like something the
  session environment places there to neutralise a repository-provided MCP configuration. I did
  not try to delete it. `git status` reports it as untracked, so the "clean working tree" check
  may flag it. Everything else is clean.
- **The first rebase was aborted by something other than my commands.** After I resolved the
  conflict and staged the file, one of my commands was rejected by the command checker before
  running, and the next `git status` showed no rebase in progress; the reflog records
  `rebase (abort): returning to refs/heads/carry/1.11.0`. I never ran `git rebase --abort`. I
  re-ran `git rebase --onto 1.11.0 1.10.1` on `carry/1.11.0`, which reproduced the same stop,
  resolved it the same way, and finished. The outcome is the one the automation would have
  reached on the first attempt; I mention it only because I cannot explain the abort.
- **SEMIO.md rewrite of the two "drop once" entries.** `git rebase` dropped the Rust 1.75 pins
  and the clippy allow by itself because they are already in 1.11.0, so SEMIO.md's text
  describing them as carried commits had become inaccurate. I rewrote those entries (details
  below) rather than deleting them. A reviewer may prefer to delete them outright, or to keep
  the old wording; this is a documentation judgment call.
- **Six upstream integration tests fail here, for an environmental reason.** They bind a TCP
  listener to `tcp/[::]:0` and the machine has no IPv6 (`Address family not supported by
  protocol`). The same tests fail identically on a pristine, detached checkout of upstream
  `1.11.0` built from scratch, so Semio's commits are not the cause. Details under Tests.

## Conflicts

### Semio commit 6bfb368317ca, "chore(config): allow clippy::redundant_field_names in zenoh-config"

- **File:** `commons/zenoh-config/src/lib.rs`
- **What upstream changed and why:** upstream commit 74051d0cc ("fix: restore Rust 1.75 and
  Clippy 1.99 compatibility (#2828)", the commit tagged `1.11.0`) added
  `#![allow(clippy::redundant_field_names)]` at the crate root with its own two-line comment
  pointing at rust-clippy issue 17525. It is the only upstream change to this file between
  1.10.1 and 1.11.0.
- **What the Semio commit needed:** the same crate-level allow, with a different one-line
  comment. SEMIO.md marks this as a CI-only commit to be dropped once upstream no longer needs
  it.
- **Resolution:** kept upstream's version of the file (`git checkout --ours`), since the Semio
  commit's intent is fully met by 74051d0cc. After staging, the commit's diff against HEAD was
  empty, so I ran `git rebase --skip` (rule 4). No conflict markers remain.
- **Confidence:** high.

No other commit conflicted. `e7a0588fd` (Codecov condition in `.github/workflows/ci.yml`)
applied cleanly: upstream's only change to that file in the range, b828c6c42 ("chore: pin
actions to commit SHA"), does not touch the Codecov steps. The runtime access control commits
applied cleanly, and none of the files they touch (`zenoh/src/api/config.rs`,
`zenoh/src/net/routing/dispatcher/tables.rs`, `zenoh/src/net/runtime/{mod,adminspace}.rs`,
`zenoh/src/tests/link_weights.rs`, `zenoh/tests/acl_runtime.rs`) was changed by upstream between
1.10.1 and 1.11.0, so the invariants in SEMIO.md (configuration read-only outside `Notifier`,
lock order configuration then routing tables) are untouched.

## Skipped Semio commits (already upstream)

| Semio commit | Already in 1.11.0 as | How it was dropped |
| --- | --- | --- |
| 5d25cd0a3 "With Rust 1.75 fix dependency to static_init 1.0.3 (#2782)" | 69f20aa83 | by `git rebase` itself ("patch contents already upstream") |
| b7968c967 "chore: pin mio-serial for Rust 1.75 compatibility on Windows (#2785)" | 9fcd9cb5d | by `git rebase` itself |
| 5d66130d2 "chore: pin hyper-util and thiserror for Rust 1.75 compatibility (#2823)" | 8f226b5d6 | by `git rebase` itself |
| 6bfb36831 "chore(config): allow clippy::redundant_field_names in zenoh-config" | 74051d0cc | conflict resolved to upstream, empty, `git rebase --skip` |

## Changes to SEMIO.md

In commit f16a89d90 ("docs: list the CI maintenance commits in SEMIO.md"), the "CI maintenance"
section now lists only the Codecov commit as carried, followed by a short paragraph and two
bullets stating that the Rust 1.75 dependency pins (69f20aa83, 9fcd9cb5d, 8f226b5d6) and the
clippy allow (74051d0cc) were carried on `semio/1.10.1`, are upstream since 1.11.0, and were
dropped by `git rebase` when carrying the line. The "Why", the upstream links and the file
names are preserved; the "Drop … once …" sentences are gone because their conditions are met
and the commits no longer exist on the line. Nothing else in SEMIO.md changed; the carrying
instructions and test commands are still accurate for 1.11.0.

"Drop … once …" conditions now satisfied (both already took effect through `git rebase`
skipping the commits; I made no removal myself):

- Dependency pins for Rust 1.75: "Drop them once the line is rebased on a release that includes
  upstream 8f226b5d6." 1.11.0 includes it.
- clippy allow: "Drop it once the line is rebased on a release whose zenoh-config passes the
  stable clippy of the time without it." 1.11.0 carries the same allow itself, so the Semio
  copy is redundant; whether zenoh-config passes without any allow is upstream's concern now.

The Codecov entry's condition ("as long as the fork runs upstream's workflow without a Codecov
token of its own") still holds; nothing to suggest there.

No file in the repository asked me to run commands, contact URLs, or change the rules.

## Tests

Run on `carry/1.11.0` after the rebase, with the repository's toolchain (Rust 1.97), as listed
in SEMIO.md's "Carrying" section.

| Command | Result |
| --- | --- |
| `cargo test -p zenoh --features unstable,internal --test acl_runtime` | ok, 6 passed |
| `cargo test -p zenoh --features unstable,internal,test --lib -- api::config::tests tests::interceptor_cache` | ok, 7 passed |
| `cargo test -p zenoh --features unstable,internal --test acl` | 9 passed, 1 failed: `test_acl_interface_names` |
| `cargo test -p zenoh --features unstable,internal --test adminspace` | ok, 6 passed |
| `cargo test -p zenoh --features unstable,internal --test interceptors` | 2 passed, 5 failed: `downsampling_by_protocol`, `downsampling_by_keyexpr`, `downsampling_by_interface`, `downsampling_reply_test`, `downsampling_query_test` |

All six failures panic in `commons/zenoh-test/src/lib.rs` while opening a session, with:

```
Can not create a new TCP listener bound to tcp/[::]:0: [Os { code: 97, kind: Uncategorized,
message: "Address family not supported by protocol" }] at io/zenoh-links/zenoh-link-tcp/src/unicast.rs:351
```

`test_acl_interface_names` sets its listener to `tcp/[::]:0` explicitly
(`zenoh/tests/acl.rs`), and the downsampling tests open a connector session whose default
listen endpoints include `[::]`. The failure reproduces outside the command sandbox and on a
pristine detached worktree of upstream tag `1.11.0` (built from the 1.11.0 sources; cargo
recompiled the carry tree afterwards, confirming the worktree build was separate). The machine
simply has no IPv6. Nothing in Semio's commits touches the TCP link, the listen defaults, or
these tests, so I made no change for them. They should pass in CI, where IPv6 is available.

The temporary worktree was removed and pruned afterwards; `git worktree list` shows only the
main checkout.


</details>

## Checks

| Result | Command | Time |
| --- | --- | --- |
| ✅ passed | `cargo metadata --no-deps --format-version 1 --offline` | 0 s |

The repository's CI, TypeScript tests included where there are any, runs on this pull request.

## SEMIO.md "drop once …" conditions (suggestions only)

- Dependency pins for Rust 1.75: **looks satisfied** (8f226b5d6 is in 1.11.0). _Drop them once the line is rebased on a release that includes upstream 8f226b5d6; `git rebase` then skips them by itself._
- `#![allow(clippy::redundant_field_names)]` in `commons/zenoh-config/src/lib.rs`: needs a human check. _Drop it once the line is rebased on a release whose zenoh-config passes the stable clippy of the time without it._

The automation never applies these removals.

## Run

Workflow run: (local run)

- `conflicts`: claude-fable-5-1, 72 turns, 884 s, $3.85, result `success`
