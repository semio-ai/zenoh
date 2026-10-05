#!/usr/bin/env bash
# "test && ok … || fail …": ok never fails, so the || only runs when the test does.
# shellcheck disable=SC2015
# End-to-end tests of fork-sync against a local forge: bare repositories that
# play eclipse-zenoh/zenoh and semio-ai/zenoh, with pull requests and issues in
# a JSON file. Nothing touches GitHub.
#
#   tests/e2e.sh [scenario ...]        # default: every scenario except "claude"
#
# Needs ZENOH_CACHE: a bare, full clone of eclipse-zenoh/zenoh with
# semio-ai/zenoh's semio/1.10.1 fetched to refs/semio/1.10.1:
#   git clone --bare https://github.com/eclipse-zenoh/zenoh.git zenoh-cache.git
#   git -C zenoh-cache.git fetch https://github.com/semio-ai/zenoh.git \
#       refs/heads/semio/1.10.1:refs/semio/1.10.1
#
# Upstream commits used as fake release tags:
#   173b1220c  semio/1.10.1 rebases onto it cleanly (3 pins dropped as upstream)
#   74051d0cc  upstream main: its own clippy allow conflicts with Semio's
#
# Scenario "claude" runs the real Claude Code CLI (costs money); others use
# tests/stub-claude.sh. E2E_REAL_CHECKS=1 runs SEMIO.md's cargo tests in the
# clean scenario instead of a quick cargo metadata.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
FS="$HERE/../scripts/fork-sync"
: "${ZENOH_CACHE:?set ZENOH_CACHE (see the header of this script)}"
ZENOH_CACHE=$(cd "$ZENOH_CACHE" && pwd)
T=${E2E_DIR:-$(mktemp -d)}
mkdir -p "$T"
CONFIG="$HERE/forks.test.yml"
# Shared by every clone, so cargo builds (checks, Claude's tests) are reused.
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-$T/cargo-target}
CLEAN_TAG=173b1220c2ab59cc22c82bfc6c95ac9971ff213b
CONFLICT_TAG=74051d0cc9aeb7304919cb02d8f7a41cf504303c
FORK_MAIN=598098b69  # one commit behind CLEAN_TAG

pass=0
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "  ok: $*"; pass=$((pass + 1)); }

sha() { git --git-dir "$ZENOH_CACHE" rev-parse "$1^{commit}"; }

# mkforge DIR UPSTREAM_MAIN FORK_MAIN [TAG=SHA ...]
mkforge() {
  local root=$1 upmain=$2 forkmain=$3
  shift 3
  rm -rf "$root"
  mkdir -p "$root/eclipse-zenoh" "$root/semio-ai"
  for r in eclipse-zenoh/zenoh semio-ai/zenoh; do
    git init -q --bare "$root/$r.git"
    echo "$ZENOH_CACHE/objects" >"$root/$r.git/objects/info/alternates"
  done
  local up="$root/eclipse-zenoh/zenoh.git" fk="$root/semio-ai/zenoh.git"
  git --git-dir "$up" update-ref refs/heads/main "$(sha "$upmain")"
  for t in 1.10.0 1.10.1; do
    git --git-dir "$up" update-ref "refs/tags/$t" "$(git --git-dir "$ZENOH_CACHE" rev-parse "refs/tags/$t")"
  done
  for spec in "$@"; do
    git --git-dir "$up" update-ref "refs/tags/${spec%%=*}" "$(sha "${spec#*=}")"
  done
  git --git-dir "$fk" update-ref refs/heads/main "$(sha "$forkmain")"
  git --git-dir "$fk" update-ref refs/heads/semio/1.10.1 "$(sha refs/semio/1.10.1)"
  echo '{"semio-ai/zenoh": "eclipse-zenoh/zenoh"}' >"$root/upstreams.json"
}

# fsrun FORGE WORK [args...]: runs fork-sync, output in WORK.log, returns its status
fsrun() {
  local forge=$1 work=$2
  shift 2
  rm -rf "$work"
  set +e
  "$FS" run --repo semio-ai/zenoh --config "$CONFIG" --local-forge "$forge" --work "$work" "$@" >"$work.log" 2>&1
  local rc=$?
  set -e
  echo "  (fork-sync exited $rc; log $work.log)"
  return $rc
}

ref() { git --git-dir "$1/semio-ai/zenoh.git" rev-parse --verify --quiet "$2" || true; }
refs_digest() { git --git-dir "$1/semio-ai/zenoh.git" for-each-ref | sha256sum; }
state() { jq -r "$2" "$1/state.json"; }

scenario_clean() {
  echo "== clean carry, then idempotency, then branch ownership"
  local F="$T/clean"
  mkforge "$F" "$CLEAN_TAG" "$FORK_MAIN" "1.10.2=$CLEAN_TAG"
  local cfg="$CONFIG"
  [ "${E2E_REAL_CHECKS:-}" = 1 ] && CONFIG="$HERE/forks.real-checks.yml"
  fsrun "$F" "$F/w1" || fail "first run failed"
  CONFIG="$cfg"
  [ "$(ref "$F" main)" = "$(sha "$CLEAN_TAG")" ] && ok "main fast-forwarded to upstream" || fail "main fast-forwarded to upstream"
  [ "$(ref "$F" semio/1.10.2)" = "$(sha "$CLEAN_TAG")" ] && ok "semio/1.10.2 created at the tag, unchanged" || fail "semio/1.10.2 created at the tag, unchanged"
  local carry
  carry=$(ref "$F" carry/1.10.2)
  [ -n "$carry" ] || fail "carry/1.10.2 not pushed"
  git --git-dir "$F/semio-ai/zenoh.git" merge-base --is-ancestor "$(sha "$CLEAN_TAG")" "$carry" && ok "carry/1.10.2 pushed on top of the tag" || fail "carry/1.10.2 pushed on top of the tag"
  [ "$(git --git-dir "$F/semio-ai/zenoh.git" rev-list --count "$CLEAN_TAG..$carry")" = 6 ] && ok "6 Semio commits carried (3 pins dropped)" || fail "6 Semio commits carried (3 pins dropped)"
  [ "$(state "$F" '.prs | length')" = 1 ] || fail "expected one PR"
  [ "$(state "$F" '.prs[0].base + " " + .prs[0].head')" = "semio/1.10.2 carry/1.10.2" ] && ok "PR carry/1.10.2 -> semio/1.10.2" || fail "PR carry/1.10.2 -> semio/1.10.2"
  [ "$(state "$F" '.prs[0].labels | join(",")')" = enhancement ] && ok "PR labelled enhancement" || fail "PR labelled enhancement"
  state "$F" '.prs[0].body' | grep -q "Dropped because upstream already contains them" && ok "PR body lists dropped commits" || fail "PR body lists dropped commits"
  state "$F" '.prs[0].body' | grep -q "None: \`git rebase\` applied every commit cleanly" && ok "PR body says no conflicts" || fail "PR body says no conflicts"
  state "$F" '.prs[0].body' | grep -q "Dependency pins for Rust 1.75: \*\*looks satisfied\*\*" && ok "PR body suggests the satisfied drop-once condition" || fail "PR body suggests the satisfied drop-once condition"
  state "$F" '.prs[0].body' >"$F/pr-body.md"

  local before_refs before_state
  before_refs=$(refs_digest "$F")
  before_state=$(sha256sum <"$F/state.json")
  fsrun "$F" "$F/w2" || fail "second run failed"
  grep -q "carry PR already open" "$F/w2.log" && ok "second run: skips (PR open)" || fail "second run: skips (PR open)"
  [ "$(refs_digest "$F")" = "$before_refs" ] && [ "$(sha256sum <"$F/state.json")" = "$before_state" ] \
    && ok "second run: no ref, PR or issue changed" || fail "second run: no ref, PR or issue changed"

  # The PR is closed without merging: the next run carries again and replaces
  # its own carry branch (force-with-lease), since no PR is open on it.
  jq '.prs[0].state = "closed"' "$F/state.json" >"$F/s" && mv "$F/s" "$F/state.json"
  sleep 1  # a different committer date gives the re-carried commits new ids
  fsrun "$F" "$F/w3" || fail "third run failed"
  grep -q "replacing fork-sync's earlier carry/1.10.2" "$F/w3.log" && ok "re-carry replaces its own carry branch" || fail "re-carry replaces its own carry branch"
  [ "$(ref "$F" carry/1.10.2)" != "$carry" ] && [ "$(state "$F" '[.prs[] | select(.state=="open")] | length')" = 1 ] \
    && ok "new PR opened for the re-carried branch" || fail "new PR opened for the re-carried branch"

  # A carry branch somebody else pushed is never overwritten.
  jq '(.prs[] | .state) = "closed"' "$F/state.json" >"$F/s" && mv "$F/s" "$F/state.json"
  git --git-dir "$F/semio-ai/zenoh.git" update-ref refs/heads/carry/1.10.2 "$(sha refs/semio/1.10.1)"
  if fsrun "$F" "$F/w4"; then fail "run over a human's carry branch succeeded"; fi
  [ "$(ref "$F" carry/1.10.2)" = "$(sha refs/semio/1.10.1)" ] && ok "a human's carry branch is left alone" || fail "a human's carry branch is left alone"
  state "$F" '.issues[0].body' | grep -q "a human owns it" && ok "failure issue explains the branch ownership" || fail "failure issue explains the branch ownership"
}

scenario_stub_resolve() {
  echo "== conflict resolved (stub Claude): PR carries the report"
  local F="$T/stub-resolve"
  mkforge "$F" "$CONFLICT_TAG" "$CLEAN_TAG" "1.11.0=$CONFLICT_TAG"
  FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=resolve fsrun "$F" "$F/w1" || fail "run failed"
  grep -q "running Claude Code (conflicts" "$F/w1.log" && ok "the Claude path ran" || fail "the Claude path ran"
  [ -n "$(ref "$F" carry/1.11.0)" ] && ok "carry/1.11.0 pushed" || fail "carry/1.11.0 pushed"
  state "$F" '.prs[0].body' | grep -q "took upstream's side" && ok "PR body includes SYNC_REPORT.md" || fail "PR body includes SYNC_REPORT.md"
  state "$F" '.prs[0].body' | grep -q "commons/zenoh-config/src/lib.rs" && ok "PR body names the conflicting file" || fail "PR body names the conflicting file"
  state "$F" '.prs[0].body' | grep -q "Needs human review" && ok "PR body flags Claude's changes for review" || fail "PR body flags Claude's changes for review"
}

scenario_claude() {
  echo "== conflict resolved by the real Claude Code CLI"
  local F="$T/claude"
  mkforge "$F" "$CONFLICT_TAG" "$CLEAN_TAG" "1.11.0=$CONFLICT_TAG"
  fsrun "$F" "$F/w1" || { tail -50 "$F/w1.log"; fail "run failed"; }
  grep -q "running Claude Code (conflicts" "$F/w1.log" && ok "the Claude path ran" || fail "the Claude path ran"
  [ -n "$(ref "$F" carry/1.11.0)" ] && ok "carry/1.11.0 pushed" || fail "carry/1.11.0 pushed"
  [ -s "$F/w1/out/SYNC_REPORT.md" ] && ok "Claude wrote SYNC_REPORT.md" || fail "Claude wrote SYNC_REPORT.md"
  state "$F" '.prs[0].body' | grep -q "Claude's report" && ok "PR body includes Claude's report" || fail "PR body includes Claude's report"
  state "$F" '.prs[0].body' >"$F/pr-body.md"
}

scenario_abort() {
  echo "== Claude gives up: failure issue, nothing pushed, idempotent, retry by version"
  local F="$T/abort"
  mkforge "$F" "$CONFLICT_TAG" "$CLEAN_TAG" "1.11.0=$CONFLICT_TAG"
  if FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=abort fsrun "$F" "$F/w1"; then
    fail "abort run reported success"
  fi
  [ -z "$(ref "$F" carry/1.11.0)" ] && ok "nothing pushed to carry/1.11.0" || fail "nothing pushed to carry/1.11.0"
  [ "$(ref "$F" semio/1.11.0)" = "$(sha "$CONFLICT_TAG")" ] && ok "semio/1.11.0 exists at the tag" || fail "semio/1.11.0 exists at the tag"
  [ "$(state "$F" '.issues | length')" = 1 ] || fail "expected one issue"
  [ "$(state "$F" '.issues[0].title')" = "fork-sync: carrying onto 1.11.0 failed" ] && ok "issue titled per spec" || fail "issue titled per spec"
  state "$F" '.issues[0].body' | grep -q "Upstream redesigned the code" && ok "issue carries SYNC_ABORT.md" || fail "issue carries SYNC_ABORT.md"
  state "$F" '.issues[0].body' | grep -q "commons/zenoh-config/src/lib.rs" && ok "issue names the conflicting file" || fail "issue names the conflicting file"
  state "$F" '.issues[0].body' | grep -q '^```diff' && ok "issue shows the conflicting hunks" || fail "issue shows the conflicting hunks"
  state "$F" '.issues[0].body' >"$F/issue-body.md"

  local before
  before=$(sha256sum <"$F/state.json")
  FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=abort fsrun "$F" "$F/w2" || fail "second run failed"
  grep -q "failure issue already open" "$F/w2.log" && [ "$(sha256sum <"$F/state.json")" = "$before" ] \
    && ok "second run: skips (issue open), changes nothing" || fail "second run: skips (issue open), changes nothing"

  if FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=abort fsrun "$F" "$F/w3" --version 1.11.0; then
    fail "forced retry reported success"
  fi
  [ "$(state "$F" '.issues | length')" = 1 ] && [ "$(state "$F" '.issues[0].comments | length')" = 1 ] \
    && ok "forced retry updates the same issue" || fail "forced retry updates the same issue"
}

scenario_unfinished() {
  echo "== Claude exits mid-rebase: failure"
  local F="$T/unfinished"
  mkforge "$F" "$CONFLICT_TAG" "$CLEAN_TAG" "1.11.0=$CONFLICT_TAG"
  if FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=leave-rebase fsrun "$F" "$F/w1"; then
    fail "unfinished rebase reported success"
  fi
  state "$F" '.issues[0].body' | grep -q "rebase still in progress" && ok "issue says the rebase was left unfinished" || fail "issue says the rebase was left unfinished"
  [ -z "$(ref "$F" carry/1.11.0)" ] && ok "nothing pushed" || fail "nothing pushed"
}

scenario_workflow_guard() {
  echo "== a workflow change Semio did not make is refused"
  local F="$T/guard"
  mkforge "$F" "$CONFLICT_TAG" "$CLEAN_TAG" "1.11.0=$CONFLICT_TAG"
  if FORK_SYNC_CLAUDE_CMD="$HERE/stub-claude.sh" STUB_BEHAVIOUR=resolve-ci fsrun "$F" "$F/w1"; then
    fail "workflow change was published"
  fi
  [ -z "$(ref "$F" carry/1.11.0)" ] && ok "nothing pushed" || fail "nothing pushed"
  state "$F" '.issues[0].body' | grep -q "changes .github/ differently" && ok "issue explains the workflow guard" || fail "issue explains the workflow guard"
}

scenario_dry_run() {
  echo "== --dry-run changes nothing"
  local F="$T/dry"
  mkforge "$F" "$CLEAN_TAG" "$FORK_MAIN" "1.10.2=$CLEAN_TAG"
  local before
  before=$(refs_digest "$F")
  fsrun "$F" "$F/w1" --dry-run || fail "dry run failed"
  [ "$(refs_digest "$F")" = "$before" ] && [ ! -e "$F/state.json" ] && ok "no ref, PR or issue changed" || fail "no ref, PR or issue changed"
  grep -q "WOULD open PR in semio-ai/zenoh: carry/1.10.2 -> semio/1.10.2" "$F/w1.log" && ok "prints the PR it would open" || fail "prints the PR it would open"
  grep -q "WOULD git push --no-verify .* [0-9a-f]\{40\}:refs/heads/carry/1.10.2" "$F/w1.log" && ok "prints the push" || fail "prints the push"
}

scenarios=("$@")
[ ${#scenarios[@]} -eq 0 ] && scenarios=(clean stub_resolve abort unfinished workflow_guard dry_run)
for s in "${scenarios[@]}"; do "scenario_${s//-/_}"; done
echo "all passed ($pass checks); artifacts in $T"
