# Q3990: stream_throttle::update_ema_if_needed — scheduler starvation

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `stream_throttle::update_ema_if_needed` and submit conflicting or low-fee transactions that make the scheduler stall or starve higher-fee ones, so that the invariant "the scheduler makes progress and honors priority ordering" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/stream_throttle.rs` -> `update_ema_if_needed`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: account conflict structure and fee of transactions it submits
- Exploit idea: Submit conflicting or low-fee transactions that make the scheduler stall or starve higher-fee ones.
- Invariant to test: the scheduler makes progress and honors priority ordering.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
