# Q4866: rpc_subscription_tracker::notify_subscribe — service-thread crash

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc_subscription_tracker::notify_subscribe` and send a request whose handling in rpc_service panics and takes down the RPC runtime, so that the invariant "no single request can panic the shared RPC service" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc_subscription_tracker.rs` -> `notify_subscribe`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the method and params of one request
- Exploit idea: Send a request whose handling in rpc_service panics and takes down the RPC runtime.
- Invariant to test: no single request can panic the shared RPC service.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
