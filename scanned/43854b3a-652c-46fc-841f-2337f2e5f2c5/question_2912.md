# Q2912: ema_function amplifies a cheap input into expensive work (stream_throttle.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `ema_function` in `streamer/src/nonblocking/stream_throttle.rs` with arguments that drive the path into its error branch after side effects were applied, and make a minimal accepted input to `ema_function` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `streamer/src/nonblocking/stream_throttle.rs` -> `ema_function()` (around line 87)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Send the smallest accepted input that makes `ema_function` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `ema_function`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. A single low-rate request or one websocket subscription from one client can consume memory, CPU, or file descriptors that grow with on-chain data instead of with an explicit bound, degrading the node without exceeding one call per CLUSTER_SLOT_TIME_TARGET / 2.
