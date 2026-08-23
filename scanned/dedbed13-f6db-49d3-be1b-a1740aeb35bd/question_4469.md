# Q4469: rpc::get_transaction_count — pubsub subscription leak

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc::get_transaction_count` and open a pubsub subscription pattern that leaks memory in rpc_subscription_tracker from a single client, so that the invariant "subscription state per client is bounded and freed on disconnect" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc.rs` -> `get_transaction_count`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the subscription methods/params it sends over one websocket
- Exploit idea: Open a pubsub subscription pattern that leaks memory in rpc_subscription_tracker from a single client.
- Invariant to test: subscription state per client is bounded and freed on disconnect.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
