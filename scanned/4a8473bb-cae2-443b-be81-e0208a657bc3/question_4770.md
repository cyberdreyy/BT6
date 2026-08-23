# Q4770: rpc_subscriptions::filter_logs_results — service-thread crash

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc_subscriptions::filter_logs_results` and send a request whose handling in rpc_service panics and takes down the RPC runtime, so that the invariant "no single request can panic the shared RPC service" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc_subscriptions.rs` -> `filter_logs_results`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the method and params of one request
- Exploit idea: Send a request whose handling in rpc_service panics and takes down the RPC runtime.
- Invariant to test: no single request can panic the shared RPC service.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
