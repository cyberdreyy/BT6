# Q4379: transaction_state_container::insert_new_transaction — chunk/frame reassembly abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `transaction_state_container::insert_new_transaction` and send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker, so that the invariant "packet reassembly memory per connection is bounded" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/transaction_state_container.rs` -> `insert_new_transaction`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the size and fragmentation of data frames it sends
- Exploit idea: Send partial or oversized QUIC chunks so packet reassembly allocates unbounded memory or stalls a worker.
- Invariant to test: packet reassembly memory per connection is bounded.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
