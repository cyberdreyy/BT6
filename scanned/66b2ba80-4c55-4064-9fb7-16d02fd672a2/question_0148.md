# Q148: run permanent bridge fund lock

## Question
Can an unprivileged attacker invoke a normal bridge flow that reaches `run` with crafted metrics and push assets into a state that cannot be redeemed, released, retried, or recovered without protocol intervention?

## Target
- File/function: crates/sui-bridge/src/eth_syncer.rs::run
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: metrics
- Exploit idea: Search for one-way state transitions, mismatched completion markers, and retry paths that can be invalidated by user-controlled sequencing.
- Invariant to test: Bridge users must always have a bounded recovery or completion path for valid locked or in-flight assets.
- Expected Immunefi impact: Critical or Medium — irreversible bridge fund lock or frozen redemption path.
- Fast validation: Use a local bridge flow, interrupt or reorder user-reachable steps, then verify whether the claim or release path remains recoverable.
