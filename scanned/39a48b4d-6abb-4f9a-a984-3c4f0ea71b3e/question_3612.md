# Q3612: purge_range amplifies a cheap input into expensive work (blockstore_purge.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `purge_range` in `ledger/src/blockstore/blockstore_purge.rs` with an interleaving where the write lands between the read and the validation, and make a minimal accepted input to `purge_range` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `ledger/src/blockstore/blockstore_purge.rs` -> `purge_range()` (around line 244)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an interleaving where the write lands between the read and the validation
- Exploit idea: Send the smallest accepted input that makes `purge_range` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `purge_range`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. A single low-rate request or one websocket subscription from one client can consume memory, CPU, or file descriptors that grow with on-chain data instead of with an explicit bound, degrading the node without exceeding one call per CLUSTER_SLOT_TIME_TARGET / 2.
