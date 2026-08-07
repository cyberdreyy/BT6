# Q2685: new_bounded cost scales with on-chain data, not with an enforced bound (evicting_sender.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `new_bounded` in `streamer/src/evicting_sender.rs` with a path that consumes the resource before the meter is charged, and make one call to `new_bounded` walk an attacker-sized on-chain structure with no parameter bound stopping it, so that the invariant "Per-request work is bounded by explicit limits, not by attacker-authored data size." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `streamer/src/evicting_sender.rs` -> `new_bounded()` (around line 31)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: a path that consumes the resource before the meter is charged
- Exploit idea: Author on-chain data so a single in-scope-rate call to `new_bounded` walks an attacker-sized structure, with no parameter limit stopping it.
- Invariant to test: Per-request work is bounded by explicit limits, not by attacker-authored data size.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Grow the on-chain structure and measure one call's time/allocations; assert they plateau.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
