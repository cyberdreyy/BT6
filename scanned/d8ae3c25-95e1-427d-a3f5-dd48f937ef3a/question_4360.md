# Q4360: transaction_state_container::push_ids_into_queue — sigverify dedup evasion

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `transaction_state_container::push_ids_into_queue` and craft packets that evade the deduper/sigverify batching so verification work is amplified per packet, so that the invariant "sigverify work per admitted packet is bounded and de-duplicated" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/transaction_state_container.rs` -> `push_ids_into_queue`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: packet contents and duplication it sends to the TPU
- Exploit idea: Craft packets that evade the deduper/sigverify batching so verification work is amplified per packet.
- Invariant to test: sigverify work per admitted packet is bounded and de-duplicated.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
