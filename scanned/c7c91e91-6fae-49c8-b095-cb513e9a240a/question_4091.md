# Q4091: sigverify::generate_packet_batches — banking-container overflow

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `sigverify::generate_packet_batches` and flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted, so that the invariant "the banking buffer is bounded and evicts lowest-priority first" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `perf/src/sigverify.rs` -> `generate_packet_batches`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume and priority of transactions it submits
- Exploit idea: Flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted.
- Invariant to test: the banking buffer is bounded and evicts lowest-priority first.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
