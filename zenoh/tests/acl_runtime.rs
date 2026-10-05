//
// Copyright (c) 2026 Semio
//
// This program and the accompanying materials are made available under the
// terms of the Eclipse Public License 2.0 which is available at
// http://www.eclipse.org/legal/epl-2.0, or the Apache License, Version 2.0
// which is available at https://www.apache.org/licenses/LICENSE-2.0.
//
// SPDX-License-Identifier: EPL-2.0 OR Apache-2.0
//

//! The access control of a running router is replaced through its configuration, by an
//! admin-space write or by `Session::config().insert_json5`, and every transport link enforces
//! the new rules at once.

#![cfg(feature = "unstable")]

use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

use zenoh::{config::WhatAmI, pubsub::Subscriber, Session};
use zenoh_config::Config;
use zenoh_core::ztimeout;
use zenoh_test::TestSessions;

const TIMEOUT: Duration = Duration::from_secs(60);
const SLEEP: Duration = Duration::from_secs(1);
const POLL: Duration = Duration::from_millis(100);
const KEY_EXPR: &str = "test/acl_runtime/data";
const VALUE: &str = "zenoh";

const PUBLISHER: &str = "a1";
const READER: &str = "b1";
const OTHER_READER: &str = "c1";
const ADMIN: &str = "d1";
const INTRUDER: &str = "e1";

/// An access control that lets `clients` put and subscribe on `test/acl_runtime/**`, and
/// [`ADMIN`] put on the admin space. Everything else is denied.
fn access_control(clients: &[&str]) -> String {
    let clients = clients
        .iter()
        .map(|zid| format!("\"{zid}\""))
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        r#"{{
            enabled: true,
            default_permission: "deny",
            rules: [
                {{
                    id: "pubsub",
                    permission: "allow",
                    flows: ["ingress", "egress"],
                    messages: ["put", "declare_subscriber"],
                    key_exprs: ["test/acl_runtime/**"],
                }},
                {{
                    id: "admin",
                    permission: "allow",
                    flows: ["ingress", "egress"],
                    messages: ["put", "declare_subscriber"],
                    key_exprs: ["@/**"],
                }},
            ],
            subjects: [
                {{ id: "clients", zids: [{clients}] }},
                {{ id: "admins", zids: ["{ADMIN}"] }},
            ],
            policies: [
                {{ id: "pubsub", rules: ["pubsub"], subjects: ["clients"] }},
                {{ id: "admin", rules: ["admin"], subjects: ["admins"] }},
            ],
        }}"#
    )
}

/// A value that deserializes but does not compile: its policy names a subject that does not
/// exist.
const INVALID_ACCESS_CONTROL: &str = r#"{
    enabled: true,
    default_permission: "allow",
    rules: [{ id: "r", permission: "deny", messages: ["put"], key_exprs: ["**"] }],
    subjects: [{ id: "s" }],
    policies: [{ rules: ["r"], subjects: ["missing"] }],
}"#;

fn router_config(clients: &[&str], adminspace_write: bool) -> Config {
    let mut config = Config::default();
    config.set_mode(Some(WhatAmI::Router)).unwrap();
    config
        .listen
        .endpoints
        .set(vec!["tcp/127.0.0.1:0".parse().unwrap()])
        .unwrap();
    config.scouting.multicast.set_enabled(Some(false)).unwrap();
    config.adminspace.set_enabled(true).unwrap();
    config
        .adminspace
        .permissions
        .set_write(adminspace_write)
        .unwrap();
    config
        .insert_json5("access_control", &access_control(clients))
        .unwrap();
    config
}

async fn open_client(test_context: &mut TestSessions, zid: &str) -> Session {
    let mut config = test_context.get_connector_config();
    config.set_mode(Some(WhatAmI::Client)).unwrap();
    config.insert_json5("id", &format!("\"{zid}\"")).unwrap();
    test_context.open_connector_with_cfg(config).await
}

type Received = Arc<Mutex<usize>>;

async fn subscribe(session: &Session) -> (Subscriber<()>, Received) {
    let received = Received::default();
    let counter = received.clone();
    let subscriber = ztimeout!(session
        .declare_subscriber(KEY_EXPR)
        .callback(move |_| *counter.lock().unwrap() += 1))
    .unwrap();
    (subscriber, received)
}

/// Publishes until every one of `receivers` got a sample.
async fn assert_delivered(publisher: &Session, receivers: &[&Received]) {
    receivers.iter().for_each(|r| *r.lock().unwrap() = 0);
    ztimeout!(async {
        while receivers.iter().any(|r| *r.lock().unwrap() == 0) {
            publisher.put(KEY_EXPR, VALUE).await.unwrap();
            tokio::time::sleep(POLL).await;
        }
    });
}

/// Publishes for a while and checks that none of `receivers` got a sample.
async fn assert_not_delivered(publisher: &Session, receivers: &[&Received]) {
    // Samples sent before this call may still be in flight.
    tokio::time::sleep(SLEEP).await;
    receivers.iter().for_each(|r| *r.lock().unwrap() = 0);
    for _ in 0..10 {
        publisher.put(KEY_EXPR, VALUE).await.unwrap();
        tokio::time::sleep(POLL).await;
    }
    tokio::time::sleep(SLEEP).await;
    for r in receivers {
        assert_eq!(*r.lock().unwrap(), 0);
    }
}

fn access_control_of(session: &Session) -> String {
    session.config().get("access_control").unwrap()
}

/// Waits until the `access_control` of `session` is no longer `previous`.
async fn wait_access_control_change(session: &Session, previous: &str) {
    ztimeout!(async {
        while access_control_of(session) == previous {
            tokio::time::sleep(POLL).await;
        }
    });
}

/// Checks, after letting any write in flight land, that the `access_control` of `session` is
/// still `previous`.
async fn assert_access_control_unchanged(session: &Session, previous: &str) {
    tokio::time::sleep(SLEEP).await;
    assert_eq!(access_control_of(session), previous);
}

fn adminspace_key(router: &Session, key: &str) -> String {
    format!("@/{}/router/config/{key}", router.zid())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_revoke_through_adminspace() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER, READER, OTHER_READER], true))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let other_reader = open_client(&mut test_context, OTHER_READER).await;
    let (_subscriber, received) = subscribe(&reader).await;
    let (_other_subscriber, other_received) = subscribe(&other_reader).await;
    assert_delivered(&publisher, &[&received, &other_received]).await;

    let transport_events = Arc::new(Mutex::new(0usize));
    let events = transport_events.clone();
    let _transport_events_listener = ztimeout!(router
        .info()
        .transport_events_listener()
        .callback(move |_| *events.lock().unwrap() += 1))
    .unwrap();

    let previous = access_control_of(&router);
    ztimeout!(router.put(
        adminspace_key(&router, "access_control"),
        access_control(&[PUBLISHER, OTHER_READER]),
    ))
    .unwrap();
    wait_access_control_change(&router, &previous).await;

    assert_not_delivered(&publisher, &[&received]).await;
    assert_delivered(&publisher, &[&other_received]).await;
    assert_eq!(
        *transport_events.lock().unwrap(),
        0,
        "no link was closed or opened"
    );

    test_context.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_grant_through_adminspace() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER], true))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let (subscriber, received) = subscribe(&reader).await;
    assert_not_delivered(&publisher, &[&received]).await;
    // The router refused that declaration; a grant does not replay it.
    ztimeout!(subscriber.undeclare()).unwrap();

    let previous = access_control_of(&router);
    ztimeout!(router.put(
        adminspace_key(&router, "access_control/subjects/id=clients"),
        format!(r#"{{ id: "clients", zids: ["{PUBLISHER}", "{READER}"] }}"#),
    ))
    .unwrap();
    wait_access_control_change(&router, &previous).await;

    let (_subscriber, received) = subscribe(&reader).await;
    assert_delivered(&publisher, &[&received]).await;

    test_context.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_invalid_write_keeps_previous_rules() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER, READER], true))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let intruder = open_client(&mut test_context, INTRUDER).await;
    let (_subscriber, received) = subscribe(&reader).await;
    let (_intruder_subscriber, intruder_received) = subscribe(&intruder).await;
    assert_delivered(&publisher, &[&received]).await;
    let previous = access_control_of(&router);

    // A whole-block write through the admin space.
    ztimeout!(router.put(
        adminspace_key(&router, "access_control"),
        INVALID_ACCESS_CONTROL
    ))
    .unwrap();
    assert_access_control_unchanged(&router, &previous).await;

    // Removing, through the admin space, a rule that a policy still names.
    ztimeout!(router.delete(adminspace_key(&router, "access_control/rules/id=pubsub"))).unwrap();
    assert_access_control_unchanged(&router, &previous).await;

    // A whole-block write in process.
    let err = router
        .config()
        .insert_json5("access_control", INVALID_ACCESS_CONTROL)
        .unwrap_err();
    assert!(err.to_string().contains("does not exist"), "{err}");
    assert_eq!(access_control_of(&router), previous);

    assert_delivered(&publisher, &[&received]).await;
    assert_not_delivered(&publisher, &[&intruder_received]).await;

    test_context.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_in_process_write() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER, READER], false))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let (_subscriber, received) = subscribe(&reader).await;
    assert_delivered(&publisher, &[&received]).await;

    router
        .config()
        .insert_json5(
            "access_control/subjects",
            &format!(
                r#"[{{ id: "clients", zids: ["{PUBLISHER}"] }}, {{ id: "admins", zids: ["{ADMIN}"] }}]"#
            ),
        )
        .unwrap();
    assert_not_delivered(&publisher, &[&received]).await;

    let previous = access_control_of(&router);
    let err = router
        .config()
        .insert_json5(
            "access_control/policies",
            r#"[{ rules: ["pubsub"], subjects: ["missing"] }]"#,
        )
        .unwrap_err();
    assert!(err.to_string().contains("does not exist"), "{err}");
    assert_eq!(access_control_of(&router), previous);

    test_context.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_adminspace_write_requires_write_permission() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER, READER], false))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let (_subscriber, received) = subscribe(&reader).await;
    assert_delivered(&publisher, &[&received]).await;

    let previous = access_control_of(&router);
    ztimeout!(router.put(
        adminspace_key(&router, "access_control"),
        access_control(&[PUBLISHER]),
    ))
    .unwrap();
    assert_access_control_unchanged(&router, &previous).await;
    assert_delivered(&publisher, &[&received]).await;

    test_context.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_acl_runtime_remote_adminspace_write_is_subject_to_access_control() {
    zenoh::init_log_from_env_or("error");
    let mut test_context = TestSessions::new();
    let router = test_context
        .open_listener_with_cfg(router_config(&[PUBLISHER, READER], true))
        .await;
    let publisher = open_client(&mut test_context, PUBLISHER).await;
    let reader = open_client(&mut test_context, READER).await;
    let admin = open_client(&mut test_context, ADMIN).await;
    let intruder = open_client(&mut test_context, INTRUDER).await;
    let (_subscriber, received) = subscribe(&reader).await;
    assert_delivered(&publisher, &[&received]).await;
    let previous = access_control_of(&router);

    ztimeout!(intruder.put(
        adminspace_key(&router, "access_control"),
        access_control(&[PUBLISHER]),
    ))
    .unwrap();
    assert_access_control_unchanged(&router, &previous).await;
    assert_delivered(&publisher, &[&received]).await;

    ztimeout!(admin.put(
        adminspace_key(&router, "access_control"),
        access_control(&[PUBLISHER]),
    ))
    .unwrap();
    wait_access_control_change(&router, &previous).await;
    assert_not_delivered(&publisher, &[&received]).await;

    test_context.close().await;
}
