# Semio's Zenoh line

This repository is Semio's fork of [Eclipse Zenoh](https://github.com/eclipse-zenoh/zenoh).
Each `semio/<version>` branch is the upstream release tag `<version>` plus the Semio commits
described below.

## Runtime access control

A running node accepts a new `access_control`, whole or in part, and enforces it on every
transport link at once, without closing any link.

- **Writing it through the admin space.** `PUT @/<zid>/<whatami>/config/access_control` with the
  whole block, `PUT …/config/access_control/<sub-key>` (for instance `subjects`), or
  `PUT …/config/access_control/<list>/id=<id>` to add or replace one item of `rules`, `subjects`
  or `policies`; `DELETE …/config/access_control/<list>/id=<id>` removes that item. The write
  needs `adminspace.enabled` and `adminspace.permissions.write`, and a remote writer's put on
  `@/…` goes through the access control of its link like any other put.
- **Writing it in process.** `Session::config().insert_json5` takes the whole block at
  `access_control` or a sub-key such as `access_control/subjects`.
- **Validation.** The new value is compiled into interceptors before anything changes. When it
  does not compile (a policy naming an unknown rule or subject, a repeated id, …), the running
  configuration and interceptors stay as they were: an in-process write returns the error, an
  admin-space write logs it at error level, since a put has no reply.
- **Revocation** takes effect on existing declarations: a revoked subscriber stops receiving on
  the link it already has.
- **A grant does not replay refused declarations.** The router drops a declaration its access
  control denies, so a newly granted client receives once it declares again: from a new session,
  or in the same session by undeclaring the refused subscriber and declaring it anew.
- **Every interceptor of a link is rebuilt.** The interceptor chain of each link is rebuilt from
  the new configuration, so downsampling filters start a new window.

The implementation is `Notifier::update` in `zenoh/src/api/config.rs`, which every runtime
configuration write goes through, and `TablesLock::set_interceptor_factories` in
`zenoh/src/net/routing/dispatcher/tables.rs`.

## Why these commits stay in the fork

ZettaScale sells runtime reconfiguration of access control in its Zenoh Commercial Edition, so
Semio keeps this change in its fork and does not offer it upstream.

## Carrying the commits to a new Zenoh release

With `upstream` pointing at `https://github.com/eclipse-zenoh/zenoh.git` and `semio` at
`https://github.com/semio-ai/zenoh.git`, for a new release `<new>` and the current line `<old>`:

```sh
git fetch upstream --tags
git fetch semio

# The new line starts as the upstream tag, unchanged.
git switch -c semio/<new> <new>
git push semio semio/<new>

# Replay Semio's commits from the current line onto the new tag.
git switch -c carry/<new> semio/semio/<old>
git rebase --onto <new> <old>
git push semio carry/<new>
gh pr create --repo semio-ai/zenoh --base semio/<new> --head carry/<new>
```

`git rebase` replays the commits and leaves out merge commits. Conflicts, if any, come from
upstream changes to `Notifier` (`zenoh/src/api/config.rs`), `TablesLock::update_config`, or
`interceptor_factories` (`zenoh/src/net/routing/interceptor/mod.rs`).

Then run the tests that cover these commits, and the upstream tests around them:

```sh
cargo test -p zenoh --features unstable,internal --test acl_runtime
cargo test -p zenoh --features unstable,internal,test --lib -- api::config::tests tests::interceptor_cache
cargo test -p zenoh --features unstable,internal --test acl --test adminspace --test interceptors
```

The pull request runs the repository's CI workflow, which runs on pull requests to any branch.
