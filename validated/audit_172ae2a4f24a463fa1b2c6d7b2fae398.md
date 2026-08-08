### Title
Single websocket connection can consume the entire node-wide subscription budget on attacker-controlled max-size accounts, causing per-connection notification cost to scale unbounded with attacker data - ([File: rpc/src/rpc_pubsub_service.rs])

### Finding Description
`handle_connection` in `rpc/src/rpc_pubsub_service.rs` only bounds the *inbound* websocket frame/message size via `builder.set_max_message_size(4_096)` / `builder.set_max_frame_size(4_096)`, at rpc/src/rpc_pubsub_service.rs:363-364. This limits the size of a single JSON-RPC request the client sends (e.g. one `accountSubscribe` call), but it does nothing to bound the number of subscriptions a connection can accumulate, nor the aggregate size of the notifications the server later pushes back out over that same connection.

Each successful subscribe request calls `RpcSolPubSubImpl::subscribe` (rpc/src/rpc_pubsub.rs:380-393), which delegates to `SubscriptionControl::subscribe` (rpc/src/rpc_subscription_tracker.rs:219-281). The only admission control is `SubscriberCountGuard::try_reserve`, which enforces a single global counter `max_active_subscriptions` (default `DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS = 1_000_000`, rpc/src/rpc_pubsub_service.rs:33) shared across **all connections on the node**, per the CLI help text: "The maximum number of active subscriptions that RPC PubSub will accept across all connections" (validator/src/commands/run/args/pub_sub_config.rs:88-90). There is no per-connection subscription cap anywhere in `handle_connection`, `RpcSolPubSubImpl`, or `SubscriptionControl` — the `current_subscriptions: Arc<DashMap<SubscriptionId, SubscriptionToken>>` in rpc/src/rpc_pubsub_service.rs:369 that tracks a single connection's live subscriptions is unbounded in size.

Consequently, one unprivileged client can issue a burst of small `accountSubscribe` requests (each well under the 4096-byte inbound frame cap) for up to the full global budget of distinct attacker-owned max-size accounts. Because each `AccountSubscriptionParams` is keyed by distinct `pubkey`, none of these subscriptions dedupe against each other (dedup in `SubscriptionControl::subscribe`, rpc/src/rpc_subscription_tracker.rs:240-260, only collapses subscriptions with *identical* params). On a slot update, the RPC notifier thread (`RpcSubscriptions::process_notifications`) produces one independent JSON notification per subscribed account and pushes it onto the shared `broadcast::Sender<RpcNotification>`. `BroadcastHandler::handle` (rpc/src/rpc_pubsub_service.rs:246-268) then forwards every notification whose `subscription_id` is present in that connection's `current_subscriptions` map to the outbound `sender`, with no per-connection cap on aggregate outbound bytes or count. The outbound side (`sender.send_binary`/similar) is not covered by the inbound `max_message_size`/`max_frame_size` limiter at all — those soketto builder settings only affect frames received from the client, not frames sent to it.

Thus a single connection's per-tick notification cost is `K * account_size`, where `K` is bounded only by the global `max_active_subscriptions` (up to 1,000,000) and `account_size` is attacker-controlled up to the maximum permitted account data length, not by any fixed per-connection cost cap.

### Impact Explanation
This is an RPC/pubsub resource-exhaustion issue: a single client can drive CPU (JSON serialization of every subscribed account on every relevant slot) and memory/bandwidth (sending up to `K` large payloads per broadcast tick) that scale with attacker-chosen data rather than any fixed per-connection budget. This falls into the "unbounded cost for a single low-rate call" category — the setup cost (issuing K subscribe calls once) is a one-time burst, but the recurring per-slot cost thereafter is entirely attacker-controlled and can starve the single-threaded/limited-thread pubsub notification pipeline (`config.notification_threads`, `config.worker_threads`) for all other RPC pubsub clients on the node, and inflate the node's memory via `RecentItems`/broadcast channel buffering (`queue_capacity_items`/`queue_capacity_bytes`, also global, not per-connection).

### Likelihood Explanation
Preconditions are minimal and match the allowed threat model: one websocket connection, standard `accountSubscribe` calls, and pre-existing attacker-controlled large accounts (on-chain data written by the attacker beforehand, which is explicitly permitted). Feasibility is high because there is no per-connection subscription-count guard to trip and no per-connection notification-byte-budget check; the only guard (`max_active_subscriptions`) is deliberately node-wide and defaults to 1,000,000, so a lone connection is free to claim a very large share of it as long as no other clients are competing for the same budget. This is fully repeatable and requires no special privileges.

### Recommendation
Add a per-connection subscription cap (and/or per-connection outbound notification byte-rate cap) in `handle_connection`/`RpcSolPubSubImpl`, independent of the global `max_active_subscriptions` counter — e.g., track `current_subscriptions.len()` against a configurable `max_subscriptions_per_connection` before calling `subscription_control.subscribe`, and/or bound aggregate outbound notification bytes per broadcast tick per connection, dropping/rate-limiting oversized bursts and disconnecting abusive clients.

### Proof of Concept
```rust
// rpc/src/rpc_pubsub_service.rs (integration-style test, tokio + soketto client)
//
// 1. Create `RpcSubscriptions` with default `PubSubConfig` (max_active_subscriptions = 1_000_000).
// 2. Write K (e.g. 2000) distinct accounts into the bank/AccountsDb, each at the maximum
//    permitted account data length, all "owned" by the attacker (no special privilege needed
//    to create accounts of that size via a system/allocate instruction in a real cluster; in
//    a unit test, insert directly into bank_forks for reproducibility of the pubsub-side bug).
// 3. Open a single websocket connection via `PubSubService`/`handle_connection` test harness
//    (see `rpc_pubsub_service::test_connection`) and send K `accountSubscribe` JSON-RPC
//    requests over that one connection, one pubkey each; assert every request succeeds
//    (K << 1_000_000).
// 4. Trigger `rpc_subscriptions.notify_slot(...)` /  account update path that marks all K
//    accounts as changed for the new slot.
// 5. Drain the connection's outbound broadcast/notification stream and sum the byte length of
//    all JSON notifications delivered for that single slot.
// 6. Assert failure of an expected per-connection bound, e.g.:
//    assert!(total_bytes_for_one_connection_one_slot > SOME_REASONABLE_PER_CONNECTION_CAP);
//    (No such cap currently exists in the code, so this test demonstrates aggregate payload
//    size scales linearly with K * account_size, unbounded by any per-connection guard.)
```