# Q1509: remove_assume_not_present arithmetic overflows on reachable values (read_only_accounts_cache.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `remove_assume_not_present` in `accounts-db/src/read_only_accounts_cache.rs` with values chosen so the arithmetic saturates, wraps, or rounds toward the attacker, and make the arithmetic in `remove_assume_not_present` overflow, wrap, or divide by zero, so that the invariant "All arithmetic on attacker-controlled values is checked or provably bounded." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `accounts-db/src/read_only_accounts_cache.rs` -> `remove_assume_not_present()` (around line 231)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: values chosen so the arithmetic saturates, wraps, or rounds toward the attacker
- Exploit idea: Supply values that make `remove_assume_not_present` overflow, so debug builds abort and release builds wrap into a nonsensical accounting value.
- Invariant to test: All arithmetic on attacker-controlled values is checked or provably bounded.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Proptest `remove_assume_not_present` across full integer ranges; assert checked arithmetic and no wrap in release mode.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
