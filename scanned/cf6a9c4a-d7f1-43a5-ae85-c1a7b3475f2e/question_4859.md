# Q4859: rpc_subscription_tracker::node_progress_watchers — service-thread crash

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc_subscription_tracker::node_progress_watchers` and send a request whose handling in rpc_service panics and takes down the RPC runtime, so that the invariant "no single request can panic the shared RPC service" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc_subscription_tracker.rs` -> `node_progress_watchers`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the method and params of one request
- Exploit idea: Send a request whose handling in rpc_service panics and takes down the RPC runtime.
- Invariant to test: no single request can panic the shared RPC service.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
