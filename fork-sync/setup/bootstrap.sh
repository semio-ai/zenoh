#!/usr/bin/env bash
# One-time setup of semio-ai/fork-sync, run by a semio-ai owner in their own
# terminal with `gh`. See SETUP.md for what each step does and why.
#
#   setup/bootstrap.sh [step ...]      # default: every step, in order
#   steps: check repo app key vars forks codeowners test
#
# Your gh login is used only to set things up: the token is never stored, and
# the automation runs as the semio-fork-sync GitHub App. Every command that
# changes the organization or another repository is shown and needs a "y".
# Secrets go from your terminal (or GitHub's API response) straight into
# `gh secret set`; nothing is written to disk or printed.
set -euo pipefail

ORG=semio-ai
REPO=fork-sync
FORKS=(zenoh zenoh-ts)
APP_SLUG=semio-fork-sync
WORK_BRANCH=${WORK_BRANCH:-initial-automation}
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/.." && pwd)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
confirm() {
  # confirm "description" cmd args...
  local desc=$1
  shift
  printf '\n%s\n  $ %s\n' "$desc" "$*"
  read -r -p "Run it? [y/N] " a
  [ "$a" = y ] || [ "$a" = Y ] || { echo "skipped"; return 1; }
  "$@"
}

step_check() {
  say "Checking your gh login"
  gh auth status
  local scopes
  scopes=$(gh api -i user 2>/dev/null | tr -d '\r' | sed -n 's/^[Xx]-[Oo][Aa]uth-[Ss]copes: //p')
  echo "token scopes: ${scopes:-unknown}"
  for s in admin:org workflow repo; do
    case ", $scopes," in *", $s,"*) ;; *)
      echo "Missing scope $s. Run: gh auth refresh -h github.com -s admin:org,workflow,repo" >&2
      exit 1 ;;
    esac
  done
  local me role
  me=$(gh api user -q .login)
  role=$(gh api "orgs/$ORG/memberships/$me" -q .role)
  echo "you are $me, $role of $ORG"
  [ "$role" = admin ] || { echo "You must be an owner (admin) of $ORG." >&2; exit 1; }
}

step_repo() {
  say "1. The central repository $ORG/$REPO"
  if gh repo view "$ORG/$REPO" >/dev/null 2>&1; then
    echo "$ORG/$REPO exists"
  else
    confirm "Create the private repository $ORG/$REPO:" \
      gh repo create "$ORG/$REPO" --private --disable-wiki \
        --description "Carries Semio's commits onto new upstream releases of its forks"
  fi
  local tmp
  tmp=$(mktemp -d)
  git clone -q "https://github.com/$ORG/$REPO.git" "$tmp/r"
  (
    cd "$tmp/r"
    if ! git rev-parse -q --verify origin/main >/dev/null; then
      # workflow_dispatch only offers workflows that exist on the default
      # branch; this placeholder makes `gh workflow run --ref <branch>` work
      # before the pull request is merged. The PR replaces it.
      git switch -q -c main
      mkdir -p .github/workflows
      cp "$HERE/placeholder-sync.yml" .github/workflows/sync.yml
      printf '# fork-sync\n\nSee the first pull request.\n' >README.md
      git add -A
      git commit -qm "Start fork-sync"
      confirm "Push the initial main (README and a placeholder sync.yml):" git push -q origin main
    fi
    git switch -q -c "$WORK_BRANCH" origin/main 2>/dev/null || git switch -q -c "$WORK_BRANCH"
    rsync -a --delete --exclude .git --exclude work --exclude .work "$SRC/" ./
    git add -A
    git commit -qm "Add the fork-sync automation" || true
    confirm "Push the branch $WORK_BRANCH:" git push -q -u origin "$WORK_BRANCH"
    confirm "Open the pull request:" gh pr create -R "$ORG/$REPO" --base main --head "$WORK_BRANCH" \
      --title "Add the fork-sync automation" --body-file "$HERE/PR_BODY.md"
  )
  rm -rf "$tmp"
}

step_app() {
  say "2. The GitHub App $APP_SLUG (manifest flow)"
  if gh api "orgs/$ORG/installations" -q ".installations[] | select(.app_slug==\"$APP_SLUG\") | .id" | grep -q .; then
    echo "$APP_SLUG is already installed in $ORG; skipping its creation."
  else
    echo "Open this page in your browser and follow it:"
    echo "  file://$HERE/create-app.html"
    read -r -p "Paste the code from the redirect URL: " code
    [[ "$code" =~ ^[0-9a-f]+$ ]] || { echo "that does not look like a code" >&2; exit 1; }
    printf '\nExchange the code for the App credentials, and store the private key and\n'
    printf 'ids as org secret/variables visible only to %s:\n' "$REPO"
    printf '  $ gh api -X POST /app-manifests/<code>/conversions | (pem -> gh secret set FORK_SYNC_APP_PRIVATE_KEY ...)\n'
    read -r -p "Run it? [y/N] " a
    if [ "$a" = y ]; then
      local resp
      resp=$(gh api -X POST "/app-manifests/$code/conversions")
      jq -r .pem <<<"$resp" | gh secret set FORK_SYNC_APP_PRIVATE_KEY --org "$ORG" --visibility selected --repos "$REPO"
      gh variable set FORK_SYNC_APP_CLIENT_ID --org "$ORG" --visibility selected --repos "$REPO" \
        --body "$(jq -r .client_id <<<"$resp")"
      gh variable set FORK_SYNC_APP_ID --org "$ORG" --visibility selected --repos "$REPO" \
        --body "$(jq -r .id <<<"$resp")"
      echo "App $(jq -r .slug <<<"$resp") created: $(jq -r .html_url <<<"$resp")"
      unset resp
    fi
  fi
  echo
  echo "Install it on $REPO and ${FORKS[*]} only (\"Only select repositories\"):"
  echo "  https://github.com/apps/$APP_SLUG/installations/new"
  read -r -p "Press Enter once it is installed. " _
  local inst
  inst=$(gh api "orgs/$ORG/installations" -q ".installations[] | select(.app_slug==\"$APP_SLUG\")")
  [ -n "$inst" ] || { echo "not installed" >&2; exit 1; }
  jq '{id, app_slug, repository_selection, permissions}' <<<"$inst"
  gh api "user/installations/$(jq -r .id <<<"$inst")/repositories" -q '.repositories[].full_name'
}

step_key() {
  say "3. The Anthropic API key"
  cat <<EOF
Use a key from Semio's own Claude Console organization (at least two admins),
ideally in a workspace of its own with a spend limit. gh prompts for the value;
paste it there, it is not echoed.
EOF
  confirm "Store it as an org secret visible only to $REPO:" \
    gh secret set ANTHROPIC_API_KEY --org "$ORG" --visibility selected --repos "$REPO"
}

step_vars() {
  say "4. Variables (repository level, on $ORG/$REPO)"
  confirm "Model for conflict resolution:" \
    gh variable set FORK_SYNC_MODEL -R "$ORG/$REPO" --body "${FORK_SYNC_MODEL:-claude-fable-5-1}" || true
  confirm "Claude Code CLI version used by the workflow:" \
    gh variable set FORK_SYNC_CLAUDE_CODE_VERSION -R "$ORG/$REPO" --body "${FORK_SYNC_CLAUDE_CODE_VERSION:-2.1.289}" || true
  echo "Optional: a Slack incoming-webhook URL (gh prompts for it). Skip if none."
  confirm "Store the Slack webhook as a repository secret:" \
    gh secret set FORK_SYNC_SLACK_WEBHOOK -R "$ORG/$REPO" || true
}

step_forks() {
  say "5. Fork settings"
  for f in "${FORKS[@]}"; do
    echo "--- $ORG/$f"
    local enabled issues
    enabled=$(gh api "repos/$ORG/$f/actions/permissions" -q .enabled)
    echo "Actions enabled: $enabled"
    if [ "$enabled" != true ]; then
      confirm "Enable Actions on $ORG/$f (its CI runs on the carry pull requests):" \
        gh api -X PUT "repos/$ORG/$f/actions/permissions" -F enabled=true || true
    fi
    issues=$(gh api "repos/$ORG/$f" -q .has_issues)
    echo "Issues enabled: $issues"
    if [ "$issues" != true ]; then
      confirm "Enable issues on $ORG/$f (fork-sync reports failures there):" \
        gh api -X PATCH "repos/$ORG/$f" -F has_issues=true || true
    fi
    if gh label list -R "$ORG/$f" --search enhancement --json name -q '.[].name' | grep -qx enhancement; then
      echo "label enhancement exists"
    else
      confirm "Create the label enhancement on $ORG/$f:" \
        gh label create enhancement -R "$ORG/$f" --color a2eeef --description "New feature or request" || true
    fi
  done
}

step_codeowners() {
  say "6. CODEOWNERS"
  echo "Who reviews changes to fork-sync? Not you: name people or a team who stay"
  echo "after you leave, e.g. '@semio-ai/platform' or '@alice @bob'."
  read -r -p "Owners: " owners
  [ -n "$owners" ] || { echo "skipped"; return; }
  local tmp
  tmp=$(mktemp -d)
  git clone -q "https://github.com/$ORG/$REPO.git" "$tmp/r"
  (
    cd "$tmp/r"
    git switch -q "$WORK_BRANCH"
    printf '# Reviewers of the fork-sync automation.\n* %s\n' "$owners" >.github/CODEOWNERS
    git add .github/CODEOWNERS
    git commit -qm "Name the code owners of fork-sync"
    confirm "Push CODEOWNERS to $WORK_BRANCH:" git push -q origin "$WORK_BRANCH"
  )
  rm -rf "$tmp"
}

step_test() {
  say "7. Handover test"
  local repo=${TEST_REPO:-$ORG/zenoh-ts}
  confirm "Run the sync workflow from $WORK_BRANCH for $repo:" \
    gh workflow run sync.yml -R "$ORG/$REPO" --ref "$WORK_BRANCH" -f repo="$repo"
  sleep 5
  local id
  id=$(gh run list -R "$ORG/$REPO" --workflow sync.yml --limit 1 --json databaseId -q '.[0].databaseId')
  gh run watch "$id" -R "$ORG/$REPO" --exit-status || true
  gh run view "$id" -R "$ORG/$REPO"
  echo
  echo "Pull requests and issues fork-sync opened (should be authored by app/$APP_SLUG):"
  for f in "${FORKS[@]}"; do
    gh pr list -R "$ORG/$f" --state all --json number,title,author,url,headRefName \
      -q '.[] | select(.headRefName | startswith("carry/")) | "\(.url) \(.author.login) \(.title)"'
    gh issue list -R "$ORG/$f" --state all --search "fork-sync: carrying onto in:title" \
      --json number,title,author,url -q '.[] | "\(.url) \(.author.login) \(.title)"'
  done
}

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(check repo app key vars forks codeowners test)
for s in "${steps[@]}"; do "step_$s"; done
