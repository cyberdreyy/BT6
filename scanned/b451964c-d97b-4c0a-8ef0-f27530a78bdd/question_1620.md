# Q1620: is_locked_readonly can be driven into unbounded work (account_locks.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `is_locked_readonly` in `accounts-db/src/account_locks.rs` with a batch crafted so scheduling reorders it relative to fee priority, and make `is_locked_readonly` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `is_locked_readonly` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `is_locked_readonly()` (around line 84)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: a batch crafted so scheduling reorders it relative to fee priority
- Exploit idea: Grow the attacker-controlled collection `is_locked_readonly` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `is_locked_readonly` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `is_locked_readonly` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
