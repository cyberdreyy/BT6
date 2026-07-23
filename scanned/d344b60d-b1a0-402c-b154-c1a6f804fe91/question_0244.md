# Q244: run_bridge_node permanent bridge fund lock

## Question
Can an unprivileged attacker invoke a normal bridge flow that reaches `run_bridge_node` with crafted config, metadata, prometheus_registry and push assets into a state that cannot be redeemed, released, retried, or recovered without protocol intervention?

## Target
- File/function: crates/sui-bridge/src/node.rs::run_bridge_node
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: config, metadata, prometheus_registry
- Exploit idea: Search for one-way state transitions, mismatched completion markers, and retry paths that can be invalidated by user-controlled sequencing.
- Invariant to test: Bridge users must always have a bounded recovery or completion path for valid locked or in-flight assets.
- Expected Immunefi impact: Critical or Medium — irreversible bridge fund lock or frozen redemption path.
- Fast validation: Use a local bridge flow, interrupt or reorder user-reachable steps, then verify whether the claim or release path remains recoverable.
