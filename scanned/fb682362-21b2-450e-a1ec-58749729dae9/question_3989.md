# Q3989: stream_throttle::update_ema_if_needed — chunk/frame reassembly abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `stream_throttle::update_ema_if_needed` and send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker, so that the invariant "packet reassembly memory per connection is bounded" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/stream_throttle.rs` -> `update_ema_if_needed`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the size and fragmentation of data frames it sends
- Exploit idea: Send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker.
- Invariant to test: packet reassembly memory per connection is bounded.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
