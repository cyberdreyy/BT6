# Q4256: receive_and_buffer::add_packet_handling_error — sigverify dedup evasion

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `receive_and_buffer::add_packet_handling_error` and craft packets that evade the deduper/sigverify batching so verification work is amplified per packet, so that the invariant "sigverify work per admitted packet is bounded and de-duplicated" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs` -> `add_packet_handling_error`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: packet contents and duplication it sends to the TPU
- Exploit idea: Craft packets that evade the deduper/sigverify batching so verification work is amplified per packet.
- Invariant to test: sigverify work per admitted packet is bounded and de-duplicated.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
