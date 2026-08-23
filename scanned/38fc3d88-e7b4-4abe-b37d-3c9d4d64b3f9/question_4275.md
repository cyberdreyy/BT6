# Q4275: receive_and_buffer::setup_transaction_view_receive_and_buffer — chunk/frame reassembly abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `receive_and_buffer::setup_transaction_view_receive_and_buffer` and send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker, so that the invariant "packet reassembly memory per connection is bounded" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs` -> `setup_transaction_view_receive_and_buffer`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the size and fragmentation of data frames it sends
- Exploit idea: Send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker.
- Invariant to test: packet reassembly memory per connection is bounded.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
