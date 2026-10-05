# Evidence from tests/e2e.sh (2026-10-05)

Output of the end-to-end tests against a local forge (see `tests/e2e.sh`):

- `pr-body-clean-carry.md`: scenario `clean`. semio/1.10.1 carried onto upstream
  `173b1220c` (playing a release `1.10.2`) without conflicts; the three
  cherry-picked Rust 1.75 pins dropped as upstream.
- `pr-body-claude-resolved.md`, `result-claude-resolved.json`: scenario `claude`,
  the real Claude Code CLI (claude-fable-5-1, 72 turns, $3.85, 15 min) carrying
  onto upstream `main` (`74051d0cc`, playing `1.11.0`), whose own clippy allow
  conflicts with Semio's. Claude skipped the redundant commit, kept every
  other commit patch-identical, updated SEMIO.md, and ran SEMIO.md's tests
  (six upstream tests fail in the test container only because it has no IPv6).
  The checks in this run were the quick `cargo metadata` of `forks.test.yml`.
- `issue-body-abort.md`: scenario `abort`, the failure issue when Claude gives up.
