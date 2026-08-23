# Q4410: rpc::default_for_test — service-thread crash

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc::default_for_test` and send a request whose handling in rpc_service panics and takes down the RPC runtime, so that the invariant "no single request can panic the shared RPC service" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc.rs` -> `default_for_test`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the method and params of one request
- Exploit idea: Send a request whose handling in rpc_service panics and takes down the RPC runtime.
- Invariant to test: no single request can panic the shared RPC service.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
