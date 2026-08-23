# Q4656: rpc::wait_for_cache_blocks — pubsub subscription leak

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc::wait_for_cache_blocks` and open a pubsub subscription pattern that leaks memory in rpc_subscription_tracker from a single client, so that the invariant "subscription state per client is bounded and freed on disconnect" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc.rs` -> `wait_for_cache_blocks`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the subscription methods/params it sends over one websocket
- Exploit idea: Open a pubsub subscription pattern that leaks memory in rpc_subscription_tracker from a single client.
- Invariant to test: subscription state per client is bounded and freed on disconnect.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
