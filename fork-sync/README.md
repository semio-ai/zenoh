# fork-sync

Carries Semio's commits onto each new upstream release of Semio's forks, and
resolves rebase conflicts with Claude Code. It runs unattended in GitHub
Actions, as the organization's `semio-fork-sync` GitHub App, with an Anthropic
API key from Semio's Claude Console organization. Nothing in it depends on a
person's account. How it was set up, and how to redo or rotate any of it, is
in [SETUP.md](SETUP.md).

## How the forks are organized

Each fork (today `semio-ai/zenoh` and `semio-ai/zenoh-ts`) has:

- `main`: a plain mirror of upstream `main`, with no Semio commit.
- `semio/<version>` release lines: each starts at the upstream tag `<version>`
  and carries Semio's commits on top. A `SEMIO.md` at the root of each line
  describes those commits, the conflict hotspots, the carry procedure and the
  tests to run. fork-sync reads it at run time; nothing about the forks'
  content is hard-coded here.

## What a run does

`.github/workflows/sync.yml` runs daily (05:17 UTC) and on demand. For each
fork in [`forks.yml`](forks.yml), in parallel, never two runs on the same fork
at once, it calls `.github/workflows/carry.yml`, which has three jobs on three
fresh runners:

1. **plan** (App token, no third-party code)
   - Fast-forwards the fork's `main` to upstream's (`merge-upstream` API; a
     non-forced `git push` if the fork is not a GitHub fork). If `main` has
     diverged, it reports it and leaves `main` alone.
   - Lists upstream's release tags (`tag_regex`, stable `x.y.z` by default) and
     the fork's `semio/*` lines. `<old>` is the newest line that carries Semio's
     commits (its tip is not the bare tag); `<new>` is upstream's newest
     release. Intermediate releases are skipped.
   - Stops if there is nothing newer, or if a `carry/<new>` pull request or a
     `fork-sync: carrying onto <new> failed` issue is open. Running twice does
     nothing new.
   - Creates `semio/<new>` at the tag, unchanged, if it does not exist.
   - Bundles `semio/<old>` for the next job.
2. **carry** (no GitHub token at all; this job runs upstream code and Claude)
   - Clones the public upstream, adds `semio/<old>` from the bundle, enables
     `git rerere`, and runs `git rebase --onto <new> <old>` on `carry/<new>`.
     Merge commits are left out, and commits upstream already has (the
     cherry-picked Rust 1.75 pins in zenoh) are dropped by git.
   - On a conflict, runs Claude Code headless with
     [`prompts/resolve-conflicts.md`](prompts/resolve-conflicts.md). Claude
     finishes the rebase and writes `SYNC_REPORT.md`, or gives up and writes
     `SYNC_ABORT.md`. The script, not Claude, then decides: success means no
     rebase in progress, `carry/<new>` checked out on top of the tag, a clean
     tree, and no merge commit.
   - Installs the toolchain the new release's `rust-toolchain.toml` pins and
     runs the checks: the `cargo` commands of SEMIO.md's "Carrying …" section,
     read from `semio/<old>`, or `checks` from `forks.yml`. If they fail,
     Claude gets one attempt to fix them, under the same rules.
   - Accounts for every Semio commit: carried, dropped because upstream has it
     (proved with `git cherry` or the `-x` trailer), or missing.
3. **publish** (App token, fresh runner, no third-party code)
   - Re-checks the bundle from scratch: based on the tag, no merge commit,
     and `.github/` changed exactly as `semio/<old>` changes it (a pull
     request runs its workflows with the fork's secrets, so a workflow change
     nobody at Semio reviewed never reaches one).
   - On success: pushes `carry/<new>` and opens a pull request into
     `semio/<new>`, labelled per `forks.yml`, listing the carried and dropped
     commits, every conflict and Claude's report, the check results, and
     SEMIO.md's "drop … once …" conditions that now look satisfied (as
     suggestions). The fork's CI, TypeScript tests included, runs on it.
   - On failure: opens or updates the issue
     `fork-sync: carrying onto <new> failed` with Claude's analysis, the
     conflicting files and hunks, and the run link. Pushes nothing.
   - Writes the job summary and, if a Slack webhook is configured, posts there.

fork-sync never merges, never pushes to `semio/*` except to create a new line
at an upstream tag, never force-pushes anything but a `carry/*` branch it made
itself (its tip committed by the App) that has no open pull request, and never
opens anything against `eclipse-zenoh/*`.

## Security model

Upstream code and anything in the repositories (code, comments, commit
messages) are third-party input, and a prompt-injection surface for Claude.

- The **carry** job, the only one that builds upstream code or runs Claude,
  has no GitHub token: it clones the public upstream and gets `semio/<old>` as
  a bundle. Its only secret is the Anthropic key, which Claude Code strips
  from every command it runs (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`, sandboxed
  with bubblewrap) and which the checks never see. Claude runs with `--bare`
  (no repository `CLAUDE.md`, hooks, plugins or MCP servers),
  `--setting-sources user`, `--permission-mode dontAsk`, a tool allowlist
  (file tools; `git`, `cargo` and read-only shell commands; no `git push`,
  `fetch`, `remote` or `config`, no web tools, no subagents), a turn limit, a
  time limit and an optional dollar budget. Its prompt tells it to treat
  repository content as data.
- The **plan** and **publish** jobs hold short-lived App tokens narrowed to one
  fork and run no third-party code. publish trusts nothing from the carry job
  but the bundle and the report text: it re-reads the tags from upstream and
  the lines from the fork, re-checks the branch, refuses `.github/` changes
  Semio did not make, and pushes only `carry/<new>`.
- Check commands come from `SEMIO.md` on the human-reviewed `semio/<old>`,
  never from the branch Claude produced, and run without a shell.
- Workflows have `permissions: {}` at the top and `contents: read` per job;
  every action is pinned to a commit SHA; checkouts use
  `persist-credentials: false`; tokens are masked in logs.
- Merging stays human: review the pull request like any external contribution.

## Reviewing a carry pull request

Read the "Needs human review" box first, then Claude's report (it lists what
it was unsure of at the top). Check the commit list: every Semio commit should
be carried or dropped with a proof. Let the fork's CI finish. Merge into
`semio/<new>` when satisfied: from then on `semio/<new>` is the line the next
release is carried from. To retry instead, close the pull request without
merging; the next run carries again and replaces `carry/<new>`.

## Running it by hand

From GitHub: **Actions → sync → Run workflow**, optionally with `repo`
(`semio-ai/zenoh`) and `version` (an upstream tag, to carry onto that release
instead of the newest; it also retries while a failure issue is open). Or:

```sh
gh workflow run sync.yml -R semio-ai/fork-sync -f repo=semio-ai/zenoh -f version=1.11.0
gh run watch -R semio-ai/fork-sync
```

Locally, without changing anything (`--dry-run` does every step, Claude and
the checks included, and prints what it would push or open):

```sh
python3 scripts/fork-sync run --repo semio-ai/zenoh --dry-run --work /tmp/fs-zenoh
```

Reads need no token for public forks; for private ones set `FORK_SYNC_TOKEN`
to a token that can read them. Claude needs `ANTHROPIC_API_KEY` (or a logged-in
`claude`), plus `bubblewrap` and `socat` (its subprocess sandbox). The phases
can also run one by one: `plan`, `carry`, `publish`, with the same `--work`.

Settings read from the environment: `FORK_SYNC_MODEL` (default
`claude-fable-5-1`), `FORK_SYNC_CLAUDE_MAX_BUDGET_USD`, `FORK_SYNC_CLAUDE_BARE=1`
(CI: `--bare`, API-key auth only), `FORK_SYNC_SLACK_WEBHOOK`,
`FORK_SYNC_CLAUDE_CMD` (tests).

## Debugging a failed run

1. Open the run (the issue and the Slack message link to it) and read the job
   summary of the failed job.
2. **plan failed**: usually access. Check that the App is installed on the fork
   (`gh api orgs/semio-ai/installations`), that `FORK_SYNC_APP_CLIENT_ID` and
   `FORK_SYNC_APP_PRIVATE_KEY` are visible to fork-sync, and that the fork's
   `semio/<old>` starts at the tag `<old>`.
3. **carry failed**: download the `carry-<fork>` artifact. It holds
   `result.json` (what happened), `SYNC_REPORT.md` / `SYNC_ABORT.md`,
   `claude-*.jsonl` (Claude's full transcript, one JSON event per line),
   `claude-*-prompt.md` (the exact prompt), `checks/*.log`, and, when the
   rebase got that far, `carry.bundle`. The job log shows one line per Claude
   tool call. Common causes: Claude gave up (read `SYNC_ABORT.md`), the turn
   limit (`subtype: error_max_turns` in `result.json`; raise
   `claude_max_turns`), the job timeout (`timeout_minutes`), a check failing
   on upstream code (carry by hand), or "Claude Code did not run" (the
   sandbox: see the "Install Claude Code" step).
4. **publish refused the branch**: the issue says why. To publish a branch
   after reviewing it yourself:

   ```sh
   gh run download <run-id> -R semio-ai/fork-sync -n carry-zenoh -D out
   git fetch out/carry.bundle carry/<new>:carry/<new>
   git push origin carry/<new>        # then open the PR by hand
   ```

5. To retry: fix the cause, then close the issue (the next daily run retries)
   or run the workflow with `version`.

## Repository layout

| Path | What |
| --- | --- |
| `forks.yml` | the forks, one entry each; adding a fork means adding an entry |
| `.github/workflows/sync.yml` | schedule, manual trigger, matrix over the forks |
| `.github/workflows/carry.yml` | the three jobs for one fork |
| `scripts/fork-sync`, `scripts/fork_sync/` | the logic (Python 3, standard library; PyYAML or `yq` for `forks.yml`) |
| `prompts/resolve-conflicts.md` | Claude's instructions |
| `tests/e2e.sh` | end-to-end tests against local bare repositories |
| `setup/` | the one-time setup: `bootstrap.sh`, the App manifest and its HTML page |

## Adding a fork

1. Give its `semio/<version>` line a `SEMIO.md` with a "Carrying …" section
   whose shell blocks hold the check commands (`cargo …` lines are used; set
   `check_programs` to use others, or `checks` to list them in `forks.yml`).
2. Add an entry to `forks.yml`.
3. Install the App on the fork (App settings → Install → add the repository)
   and make sure the fork has issues, Actions and the PR labels it needs.

## Tests

```sh
git clone --bare https://github.com/eclipse-zenoh/zenoh.git /tmp/zenoh-cache.git
git -C /tmp/zenoh-cache.git fetch https://github.com/semio-ai/zenoh.git refs/heads/semio/1.10.1:refs/semio/1.10.1
ZENOH_CACHE=/tmp/zenoh-cache.git tests/e2e.sh              # stub Claude, quick checks
ZENOH_CACHE=/tmp/zenoh-cache.git tests/e2e.sh claude       # the real Claude Code (costs money)
```

They build a local forge where upstream commit `173b1220c` plays a release
`semio/1.10.1` carries onto cleanly, and upstream `main` (`74051d0cc`, which
adds its own `clippy::redundant_field_names` allow) one that conflicts.
