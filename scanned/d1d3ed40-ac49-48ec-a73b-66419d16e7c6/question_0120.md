# Q120: get_events_in_range permanent bridge fund lock

## Question
Can an unprivileged attacker invoke a normal bridge flow that reaches `get_events_in_range` with crafted address, start_block, end_block and push assets into a state that cannot be redeemed, released, retried, or recovered without protocol intervention?

## Target
- File/function: crates/sui-bridge/src/eth_client.rs::get_events_in_range
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: address, start_block, end_block
- Exploit idea: Search for one-way state transitions, mismatched completion markers, and retry paths that can be invalidated by user-controlled sequencing.
- Invariant to test: Bridge users must always have a bounded recovery or completion path for valid locked or in-flight assets.
- Expected Immunefi impact: Critical or Medium — irreversible bridge fund lock or frozen redemption path.
- Fast validation: Use a local bridge flow, interrupt or reorder user-reachable steps, then verify whether the claim or release path remains recoverable.
