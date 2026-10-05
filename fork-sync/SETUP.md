# Setting up fork-sync

Everything fork-sync needs, where it lives, and how to redo it. The automation
authenticates only as the **semio-fork-sync** GitHub App (owned by the
semio-ai organization) and with an **Anthropic API key from Semio's Claude
Console organization**. No personal account, personal access token, or
personal Claude account is involved at run time, so the person who set it up
can leave without breaking it.

[`setup/bootstrap.sh`](setup/bootstrap.sh) performs the steps below with `gh`.
Run it in your own terminal as an owner of semio-ai; it shows every command
that changes the organization or a repository and runs it only after you
answer `y`. Run one step with `setup/bootstrap.sh <step>`.

```sh
gh auth refresh -h github.com -s admin:org,workflow,repo   # if the check step asks
setup/bootstrap.sh            # check repo app key vars forks codeowners test
```

Your `gh` login is used only to set things up. Its token is never stored as a
secret, written to a file, or used by a workflow.

## What is set up, and where

| What | Where | Used by |
| --- | --- | --- |
| Repository `semio-ai/fork-sync` (private) | GitHub, semio-ai | holds the automation |
| GitHub App `semio-fork-sync` | semio-ai → Settings → Developer settings → GitHub Apps | every write to the forks |
| App installation | on `fork-sync`, `zenoh`, `zenoh-ts` only | — |
| `FORK_SYNC_APP_CLIENT_ID` | org **variable**, visible to fork-sync only | `create-github-app-token` |
| `FORK_SYNC_APP_ID` | org variable, visible to fork-sync only | reference (numeric id) |
| `FORK_SYNC_APP_PRIVATE_KEY` | org **secret**, visible to fork-sync only | `create-github-app-token` |
| `ANTHROPIC_API_KEY` | org secret, visible to fork-sync only | Claude Code in the carry job |
| `FORK_SYNC_MODEL` | repo variable on fork-sync (default `claude-fable-5-1`) | Claude Code |
| `FORK_SYNC_CLAUDE_CODE_VERSION` | repo variable (default `2.1.289`) | the CLI version installed |
| `FORK_SYNC_CLAUDE_MAX_BUDGET_USD` | repo variable, optional | `--max-budget-usd` per Claude run |
| `FORK_SYNC_CLAUDE_TIMEOUT_MINUTES` | repo variable, optional (default 120) | Claude's time limit per run |
| `FORK_SYNC_SLACK_WEBHOOK` | repo secret, optional | notifications |
| Forks: Actions enabled, issues enabled, label `enhancement` | `semio-ai/zenoh`, `semio-ai/zenoh-ts` | PR CI, failure issues, label check |
| `.github/CODEOWNERS` | fork-sync | reviewers of changes to the automation |

## The steps

### 0. check

`gh auth status`; the token needs `admin:org` (org secrets and variables),
`workflow` (pushing `.github/workflows/`) and `repo`. You must be an owner:
`gh api orgs/semio-ai/memberships/$(gh api user -q .login) -q .role` prints
`admin`.

### 1. repo

`gh repo create semio-ai/fork-sync --private`, then:

- `main` gets a README and a placeholder `sync.yml` that only has
  `workflow_dispatch`. GitHub offers manual runs only for workflows on the
  default branch; the placeholder makes `gh workflow run sync.yml --ref
  <branch>` (step 7) work before the pull request is merged. The pull request
  replaces it.
- The automation goes on the branch `initial-automation`, and a pull request
  into `main` is opened. A CODEOWNER other than the setup author reviews and
  merges it.

### 2. app

The App is created from [`setup/app-manifest.json`](setup/app-manifest.json)
(Contents, Pull requests, Issues, Workflows: write; Metadata: read; no webhook;
private to semio-ai) through GitHub's manifest flow:

1. Open [`setup/create-app.html`](setup/create-app.html) in a browser; it POSTs
   the manifest to `https://github.com/organizations/semio-ai/settings/apps/new`.
   (The page embeds a copy of the manifest; keep the two in sync.)
2. Click **Create GitHub App**. GitHub redirects to
   `https://github.com/semio-ai/fork-sync?code=…`; paste the code into the script.
3. The script exchanges it with `gh api -X POST /app-manifests/<code>/conversions`
   and pipes the response's `pem` straight into
   `gh secret set FORK_SYNC_APP_PRIVATE_KEY --org semio-ai --visibility selected --repos fork-sync`,
   and stores `client_id` and `id` as org variables the same way. The response
   is held in a shell variable only; nothing is printed or written.
4. Install the App at `https://github.com/apps/semio-fork-sync/installations/new`,
   choosing **Only select repositories**: `fork-sync`, `zenoh`, `zenoh-ts`.
   The script then checks `gh api orgs/semio-ai/installations`.

Workflows: write is needed because fast-forwarding `main`, creating
`semio/<new>` at an upstream tag, and pushing a carry branch all bring in
changes to `.github/workflows/` (the zenoh carry changes `ci.yml`). Each job
mints a token narrowed to one fork and to the permissions that job needs.

Org owners can always manage the App. To let someone else manage it without
being an owner, add them under the App's settings → **App managers**.

### 3. key

Create the key in **Semio's own Claude Console organization** (not a personal
one), which must have at least two admins, preferably in a workspace dedicated
to fork-sync with a monthly spend limit. Then:

```sh
gh secret set ANTHROPIC_API_KEY --org semio-ai --visibility selected --repos fork-sync
```

`gh` prompts for the value; paste it there.

### 4. vars

`FORK_SYNC_MODEL`, `FORK_SYNC_CLAUDE_CODE_VERSION` and, optionally,
`FORK_SYNC_SLACK_WEBHOOK` (a Slack incoming webhook; fork-sync works without it).

### 5. forks

For each fork: Actions enabled (`gh api repos/semio-ai/<fork>/actions/permissions`;
the carry pull request runs the fork's CI), issues enabled (GitHub disables
them on forks by default; failure reports go there), and the `enhancement`
label (upstream's label-checklist workflow fails a pull request without a
label).

### 6. codeowners

`.github/CODEOWNERS` names who reviews changes to fork-sync: people or a team
who are not the setup author. Consider a ruleset on `main` requiring a
CODEOWNER review.

### 7. test

```sh
gh workflow run sync.yml -R semio-ai/fork-sync --ref initial-automation -f repo=semio-ai/zenoh-ts
gh run watch -R semio-ai/fork-sync
```

Then check that whatever it changed was done by the App: a pull request or
issue it opened is authored by `semio-fork-sync[bot]`, and a fast-forward of
`main` shows the App as the actor in the repository's activity
(`https://github.com/semio-ai/<fork>/activity`). With no new upstream release,
a run only mirrors `main` and reports "up to date"; the first carry pull
request appears with the next upstream release.

## Rotating credentials

**App private key** (yearly, or at once if exposed):

1. semio-ai → Settings → Developer settings → GitHub Apps → semio-fork-sync →
   **Generate a private key** (a `.pem` downloads).
2. `gh secret set FORK_SYNC_APP_PRIVATE_KEY --org semio-ai --visibility selected --repos fork-sync < ~/Downloads/semio-fork-sync.*.private-key.pem`
3. Run the workflow once (step 7), then delete the old key in the App settings
   and delete the downloaded file.

**Anthropic API key**: create a new key in Semio's Console organization, set
it with the `gh secret set ANTHROPIC_API_KEY …` command of step 3, run the
workflow once, then disable the old key in the Console.

**Slack webhook**: create a new webhook, `gh secret set FORK_SYNC_SLACK_WEBHOOK -R semio-ai/fork-sync`,
revoke the old one.

## Handing over

- The App, the secrets and the variables belong to the organization. Org
  owners can rotate everything above without the original author.
- The Claude Console organization needs at least two admins at all times.
- Keep CODEOWNERS current with people who are still at Semio.
- If the App is deleted, redo step 2: a new App gets new ids, which the
  script stores again; nothing else changes.
