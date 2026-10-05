#!/usr/bin/env bash
# Stands in for the claude CLI in tests (FORK_SYNC_CLAUDE_CMD). Runs in the
# carry clone like Claude would; STUB_BEHAVIOUR picks what it does:
#   abort           give up: abort the rebase, write SYNC_ABORT.md
#   resolve         take upstream's side of every conflict, skip emptied commits
#   resolve-ci      like resolve, then also edit a workflow file (must be refused)
#   leave-rebase    exit with the rebase still in progress
# It prints a stream-json result line like the real CLI.
set -euo pipefail
: "${FORK_SYNC_REPORT_PATH:?}" "${FORK_SYNC_ABORT_PATH:?}" "${FORK_SYNC_REPO_DIR:?}"
cd "$FORK_SYNC_REPO_DIR"
export GIT_EDITOR=true

resolve() {
  while [ -d "$(git rev-parse --git-dir)/rebase-merge" ] || [ -d "$(git rev-parse --git-dir)/rebase-apply" ]; do
    for f in $(git diff --name-only --diff-filter=U); do
      git checkout --ours -- "$f"   # during a rebase, "ours" is the upstream side
      git add -- "$f"
      echo "- \`$f\` in $(git log -1 --format='%h %s' REBASE_HEAD): took upstream's side (confidence: high)" >>"$FORK_SYNC_REPORT_PATH"
    done
    if git diff --cached --quiet; then
      echo "- skipped $(git log -1 --format='%h %s' REBASE_HEAD): empty once upstream's side is taken" >>"$FORK_SYNC_REPORT_PATH"
      git rebase --skip >/dev/null 2>&1 || true
    else
      git rebase --continue >/dev/null 2>&1 || true
    fi
  done
}

printf '## Needs human review\n\nNothing.\n\n## Conflicts (stub)\n\n' >"$FORK_SYNC_REPORT_PATH"
case "${STUB_BEHAVIOUR:-resolve}" in
  abort)
    git rebase --abort
    printf '# Cannot carry\n\nUpstream redesigned the code the feature hooks into (stub).\n' >"$FORK_SYNC_ABORT_PATH"
    ;;
  resolve)
    resolve
    ;;
  resolve-ci)
    resolve
    echo "# injected" >>.github/workflows/ci.yml
    git commit -qam "ci: injected change"
    ;;
  leave-rebase)
    ;;
esac
echo '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0,"result":"stub done"}'
