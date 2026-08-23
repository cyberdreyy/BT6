# Q4029: quic::default_num_tpu_vote_transaction_receive_threads — chunk/frame reassembly abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `quic::default_num_tpu_vote_transaction_receive_threads` and send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker, so that the invariant "packet reassembly memory per connection is bounded" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/quic.rs` -> `default_num_tpu_vote_transaction_receive_threads`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the size and fragmentation of data frames it sends
- Exploit idea: Send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker.
- Invariant to test: packet reassembly memory per connection is bounded.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
