# Q4193: packet::index — chunk/frame reassembly abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `packet::index` and send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker, so that the invariant "packet reassembly memory per connection is bounded" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `perf/src/packet.rs` -> `index`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the size and fragmentation of data frames it sends
- Exploit idea: Send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker.
- Invariant to test: packet reassembly memory per connection is bounded.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
