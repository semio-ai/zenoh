# Semio's Zenoh line

This repository is Semio's fork of [Eclipse Zenoh](https://github.com/eclipse-zenoh/zenoh).
Each `semio/<version>` branch is the upstream release tag `<version>` plus the Semio commits
described below: one feature, runtime access control, and the maintenance that keeps the
repository's CI passing on the line.

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
`zenoh/src/net/routing/dispatcher/tables.rs`. Two rules keep it sound:

- **The configuration is read-only outside `Notifier`'s methods.** `Notifier::lock()`, which
  in-process code reaches through `Runtime::config()`, returns a guard without mutable access,
  so no caller can change `access_control` without the validation and re-arming above. This
  departs from upstream, where the guard is mutable.
- **Lock order: the runtime configuration, then the routing tables.** `Notifier::update` holds
  the configuration lock while it re-arms the faces, so once the runtime is built, code holding
  a routing-tables lock must not lock the configuration. Hat initialization does so only while
  the runtime is being built, and `Runtime::update_network` is only called from tests.

### Why it stays in the fork

ZettaScale sells runtime reconfiguration of access control in its Zenoh Commercial Edition, so
Semio keeps this change in its fork and does not offer it upstream.

## CI maintenance

Each of these commits exists only so that CI passes on the line; none changes Zenoh's
behaviour.

- **Dependency pins for Rust 1.75**: upstream commits
  [69f20aa83](https://github.com/eclipse-zenoh/zenoh/commit/69f20aa83165ae5747d0314711c0d0079752942a),
  [9fcd9cb5d](https://github.com/eclipse-zenoh/zenoh/commit/9fcd9cb5d364192c3e8a27e66de76f4bc750d1d5)
  and
  [8f226b5d6](https://github.com/eclipse-zenoh/zenoh/commit/8f226b5d67663fa361d9c44b4b94a5842c3c7bc9),
  cherry-picked with `-x`, in `commons/zenoh-pinned-deps-1-75/Cargo.toml`. Dependency versions
  published after 1.10.1 need a newer Cargo than 1.75, which the "Check zenoh using Rust 1.75"
  job uses. Drop them once the line is rebased on a release that includes upstream 8f226b5d6;
  `git rebase` then skips them by itself.
- **`#![allow(clippy::redundant_field_names)]` in `commons/zenoh-config/src/lib.rs`.** Clippy
  1.99 reports `field: field` in the code that the `validated_struct::validator!` expansion
  generates, and the lint jobs deny warnings. The expansion's impls sit at the crate root, so
  the allow covers the crate. Drop it once the line is rebased on a release whose zenoh-config
  passes the stable clippy of the time without it.
- **Codecov uploads run only in `eclipse-zenoh/zenoh`** (`.github/workflows/ci.yml`). The
  upload steps need upstream's Codecov token, which the fork does not have; without it they
  fail every test job and the coverage job. Keep it for as long as the fork runs upstream's
  workflow without a Codecov token of its own. It changes nothing upstream.

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

`git rebase` replays the commits, leaves out merge commits, and skips a cherry-picked upstream
commit that the new release already contains. Conflicts, if any, come from upstream changes to
`Notifier` (`zenoh/src/api/config.rs`), `TablesLock::update_config`, `interceptor_factories`
(`zenoh/src/net/routing/interceptor/mod.rs`), or the Codecov steps of
`.github/workflows/ci.yml`. A token without the `workflow` scope cannot push a change to
`.github/workflows/`; push the branch over SSH instead.

Give the pull request a label, for instance `enhancement`: upstream's label-checklist
workflow fails a pull request without one.

Then run the tests that cover these commits, and the upstream tests around them:

```sh
cargo test -p zenoh --features unstable,internal --test acl_runtime
cargo test -p zenoh --features unstable,internal,test --lib -- api::config::tests tests::interceptor_cache
cargo test -p zenoh --features unstable,internal --test acl --test adminspace --test interceptors
```

The pull request runs the repository's CI workflow, which runs on pull requests to any branch.
