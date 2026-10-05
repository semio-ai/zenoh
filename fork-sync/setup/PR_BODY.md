Adds fork-sync: a daily GitHub Actions automation that keeps Semio's forks
(`semio-ai/zenoh`, `semio-ai/zenoh-ts`) carried onto each new upstream release.

- `main` of each fork is fast-forwarded to upstream (`merge-upstream`).
- When upstream publishes a release newer than the newest `semio/<version>`
  line, `semio/<new>` is created at the tag and Semio's commits are rebased onto
  it on `carry/<new>`. Conflicts go to Claude Code, headless, following
  `prompts/resolve-conflicts.md` and each line's `SEMIO.md`.
- The checks of SEMIO.md's "Carrying" section run; the result is a pull
  request `carry/<new>` → `semio/<new>` in the fork, or a failure issue.
- Runs as the org-owned `semio-fork-sync` GitHub App with an Anthropic key
  from Semio's Console organization; the job that runs upstream code and Claude
  holds no GitHub token. It never merges, never force-pushes `semio/*`.

See README.md (how it works, running by hand, debugging) and SETUP.md (what
was set up and how to rotate it). `tests/e2e.sh` exercises a clean carry, a
conflict resolved by Claude, Claude giving up, idempotency, and the guards,
against local repositories.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
