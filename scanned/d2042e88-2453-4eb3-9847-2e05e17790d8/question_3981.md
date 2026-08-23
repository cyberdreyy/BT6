# Q3981: connection_rate_limiter::poc_is_allowed_peek_does_not_reserve — sigverify dedup evasion

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `connection_rate_limiter::poc_is_allowed_peek_does_not_reserve` and craft packets that evade the deduper/sigverify batching so verification work is amplified per packet, so that the invariant "sigverify work per admitted packet is bounded and de-duplicated" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/connection_rate_limiter.rs` -> `poc_is_allowed_peek_does_not_reserve`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: packet contents and duplication it sends to the TPU
- Exploit idea: Craft packets that evade the deduper/sigverify batching so verification work is amplified per packet.
- Invariant to test: sigverify work per admitted packet is bounded and de-duplicated.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
