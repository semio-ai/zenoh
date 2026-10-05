"""Markdown for pull requests, issues, job summaries and Slack."""

from __future__ import annotations

from .util import head

GITHUB_BODY_LIMIT = 65000


def _link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url.startswith("http") else url


def _commit_line(c: dict, repo: str) -> str:
    new = f" → `{c['new_sha'][:12]}`" if c.get("new_sha") else ""
    return f"- `{c['sha'][:12]}`{new} {c['subject']}"


def _checks_table(checks: list[dict]) -> str:
    if not checks:
        return "_No checks ran._"
    rows = ["| Result | Command | Time |", "| --- | --- | --- |"]
    icon = {"passed": "✅ passed", "failed": "❌ failed", "timed out": "⏱️ timed out"}
    for c in checks:
        rows.append(f"| {icon.get(c['status'], c['status'])} | `{c['command']}` | {c['seconds']} s |")
    return "\n".join(rows)


def _claude_runs(runs: list[dict]) -> str:
    if not runs:
        return "Claude Code was not needed."
    out = []
    for r in runs:
        cost = f", ${r['cost_usd']:.2f}" if isinstance(r.get("cost_usd"), (int, float)) else ""
        out.append(f"- `{r['mode']}`: {r.get('model')}, {r.get('turns')} turns, {r.get('seconds')} s{cost}, "
                   f"result `{r.get('subtype')}`" + (" (timed out)" if r.get("timed_out") else ""))
    return "\n".join(out)


def review_flags(res: dict) -> list[str]:
    flags = []
    commits = res.get("commits") or {}
    for c in commits.get("dropped_unproven", []):
        flags.append(f"Semio commit `{c['sha'][:12]}` ({c['subject']}) is not on the carried branch and the "
                     "automation could not prove upstream already has it.")
    for c in commits.get("added", []):
        flags.append(f"Commit `{c['sha'][:12]}` ({c['subject']}) does not correspond to a Semio commit of the "
                     "old line.")
    if res.get("semio_md_changed"):
        flags.append("`SEMIO.md` was changed during the carry; check that it still describes the line.")
    if res.get("claude_runs"):
        flags.append("Claude Code changed code during this carry: read its report below, starting with "
                     "its \"Needs human review\" section.")
    return flags


def pr_title(res: dict) -> str:
    return f"Carry Semio's commits onto {res['new']}"


def pr_body(plan: dict, res: dict, run_url: str) -> str:
    old, new, repo = res["old"], res["new"], plan["repo"]
    commits = res.get("commits") or {}
    parts = [
        f"Carries Semio's commits from `semio/{old}` onto the upstream release `{new}` "
        f"([{plan['upstream']}@{new}](https://github.com/{plan['upstream']}/releases/tag/{new})).",
        f"Opened by fork-sync ({_link('run', run_url)}). Merging is a human decision; the automation "
        "never merges.",
    ]
    flags = review_flags(res)
    if flags:
        parts.append("> [!WARNING]\n> **Needs human review**\n" + "\n".join(f"> - {f}" for f in flags))

    parts.append("## Commits")
    parts.append("Carried:\n" + ("\n".join(_commit_line(c, repo) for c in commits.get("carried", [])) or "_none_"))
    if commits.get("dropped_upstream"):
        parts.append("Dropped because upstream already contains them:\n" + "\n".join(
            f"- `{c['sha'][:12]}` {c['subject']} — {c['proof']}" for c in commits["dropped_upstream"]))
    if commits.get("dropped_unproven"):
        parts.append("**Missing, not proven to be upstream:**\n" + "\n".join(
            f"- `{c['sha'][:12]}` {c['subject']}" for c in commits["dropped_unproven"]))
    if commits.get("added"):
        parts.append("**Commits without a counterpart on the old line:**\n" + "\n".join(
            f"- `{c['sha'][:12]}` {c['subject']}" for c in commits["added"]))

    parts.append("## Conflicts")
    if not res.get("conflicts"):
        parts.append("None: `git rebase` applied every commit cleanly.")
    else:
        first = res["conflicts"][0]
        parts.append(f"The rebase first stopped on `{first['commit'][:12]}` ({first['subject']}), in "
                     + ", ".join(f"`{f}`" for f in first["files"]) + ". Claude Code resolved it and any later "
                     "conflicts. Its report (`SYNC_REPORT.md`):")
    if res.get("report_md"):
        parts.append("<details open><summary>Claude's report</summary>\n\n" + head(res["report_md"], 30000)
                     + "\n\n</details>")
    elif res.get("claude_runs"):
        parts.append("_Claude did not write a report._")

    parts.append("## Checks")
    if res.get("checks_before_fix"):
        parts.append("Before Claude's fix:\n\n" + _checks_table(res["checks_before_fix"]) + "\n\nAfter:")
    parts.append(_checks_table(res.get("checks", [])))
    parts.append("The repository's CI, TypeScript tests included where there are any, runs on this pull request.")

    sugg = res.get("drop_suggestions") or []
    if sugg:
        parts.append("## SEMIO.md \"drop once …\" conditions (suggestions only)")
        lines = []
        for s in sugg:
            verdict = {True: "**looks satisfied**", False: "not satisfied yet", None: "needs a human check"}[
                s.get("looks_satisfied")]
            ev = f" ({'; '.join(s['evidence'])})" if s.get("evidence") else ""
            lines.append(f"- {s['title'].rstrip('.')}: {verdict}{ev}. _{s['condition']}_")
        parts.append("\n".join(lines) + "\n\nThe automation never applies these removals.")

    parts.append("## Run\n\n" + f"Workflow run: {run_url}\n\n" + _claude_runs(res.get("claude_runs", [])))
    return _fit("\n\n".join(parts))


def issue_body(plan: dict, res: dict | None, reason: str, run_url: str) -> str:
    new = plan.get("new")
    old = plan.get("old")
    parts = [
        f"fork-sync could not carry Semio's commits from `semio/{old}` onto the upstream release `{new}`.",
        f"**Reason:** {reason}",
        f"**Run:** {run_url}",
        "Nothing was pushed to `carry/*`. "
        f"`semio/{new}` exists at the upstream tag, unchanged. Carry by hand following SEMIO.md, "
        "or fix the cause and retry: close this issue (the next daily run retries), or run the "
        f"`sync` workflow with `repo={plan['repo']}` and `version={new}`.",
    ]
    if res:
        if res.get("abort_md"):
            parts.append("## Claude's analysis (`SYNC_ABORT.md`)\n\n" + head(res["abort_md"], 20000))
        if res.get("report_md"):
            parts.append("<details><summary>Claude's report (<code>SYNC_REPORT.md</code>)</summary>\n\n"
                         + head(res["report_md"], 20000) + "\n\n</details>")
        for c in res.get("conflicts", []):
            parts.append(f"## Conflict replaying `{c['commit'][:12]}` ({c['subject']})\n\nFiles: "
                         + ", ".join(f"`{f}`" for f in c["files"])
                         + "\n\n<details><summary>Conflicting hunks</summary>\n\n```diff\n"
                         + head(c.get("diff", ""), 15000).replace("```", "``​`") + "\n```\n\n</details>")
        if res.get("checks"):
            parts.append("## Checks\n\n" + _checks_table(res["checks"]))
            for c in res["checks"]:
                if c["status"] != "passed":
                    parts.append(f"<details><summary>Log tail: <code>{c['command']}</code></summary>\n\n```\n"
                                 + c.get("log_tail", "").replace("```", "``​`") + "\n```\n\n</details>")
        if res.get("claude_runs"):
            parts.append("## Claude Code runs\n\n" + _claude_runs(res["claude_runs"]))
    parts.append("The run's artifacts hold the full logs, Claude's transcript and, when the rebase got "
                 "that far, a bundle of the attempted branch.")
    return _fit("\n\n".join(parts))


def _fit(text: str) -> str:
    if len(text) <= GITHUB_BODY_LIMIT:
        return text
    return text[: GITHUB_BODY_LIMIT - 200] + "\n\n_[Truncated: the full report is in the run's artifacts.]_"
