# Q4411: rpc::clone_without_bigtable — account-resolver panic

## Question
Can an unprivileged attacker, through a single JSON-RPC/pubsub request from an unprivileged client, reach `rpc::clone_without_bigtable` and send parameters that drive account_resolver into an unwrap/index panic, so that the invariant "malformed account params are rejected without panicking the RPC thread" is violated, leading to RPC DoS/Crash?

## Target
- File/function: `rpc/src/rpc.rs` -> `clone_without_bigtable`
- Entrypoint: a single JSON-RPC/pubsub request from an unprivileged client
- Attacker controls: the account address/encoding params in one request
- Exploit idea: Send parameters that drive account_resolver into an unwrap/index panic.
- Invariant to test: malformed account params are rejected without panicking the RPC thread.
- Expected Immunefi impact: RPC DoS/Crash — High
- Fast validation: write an rpc test issuing the single crafted request and asserting bounded memory/time and no panic.
