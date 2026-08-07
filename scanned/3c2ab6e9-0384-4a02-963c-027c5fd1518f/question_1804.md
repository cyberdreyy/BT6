# Q1804: get_program_kind can be driven into unbounded work (builtin_programs_filter.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `get_program_kind` in `compute-budget-instruction/src/builtin_programs_filter.rs` with a lookup whose result is cached and then invalidated by the attacker's own write, and make `get_program_kind` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `get_program_kind` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `compute-budget-instruction/src/builtin_programs_filter.rs` -> `get_program_kind()` (around line 37)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: a lookup whose result is cached and then invalidated by the attacker's own write
- Exploit idea: Grow the attacker-controlled collection `get_program_kind` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `get_program_kind` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `get_program_kind` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
