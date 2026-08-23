# Q3969: quic::poc_staked_eviction_committed_before_failed_admit — stream throttle bypass

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `quic::poc_staked_eviction_committed_before_failed_admit` and open streams faster than stream_throttle allows so a single peer consumes disproportionate ingest capacity, so that the invariant "per-connection stream/byte rate is bounded on default config" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/quic.rs` -> `poc_staked_eviction_committed_before_failed_admit`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the number and pacing of QUIC streams it opens
- Exploit idea: Open streams faster than stream_throttle allows so a single peer consumes disproportionate ingest capacity.
- Invariant to test: per-connection stream/byte rate is bounded on default config.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
